/** RunStore — one Durable Object per run. Absorbs the metric/histogram firehose
 * that would otherwise hammer D1 one row per point.
 *
 * Why a DO: it is single-threaded per run, so the ingest watermark needs no
 * read-then-write lock (the old sync_progress dance), and the run's whole time
 * series can live in the DO's own SQLite — entirely off D1.
 *
 * What still touches D1: a single `runs` row write per ingest batch carrying the
 * rolled-up summary + stats + liveness (same cadence as the old `touchRun`, not a
 * regression). That keeps `GET /api/runs` a pure catalog read — no per-poll
 * aggregate scan over millions of metric rows, and the list endpoint sees fresh
 * stats the instant a batch lands (no alarm lag).
 *
 * Storage shape: points are appended as one **segment** row per ingest batch
 * (a columnar JSON blob of {steps,values,ts}), never one row per point. Reads
 * merge a key's segments and stride-sample, mirroring db.ts's SQL downsampler. */

import { DurableObject } from 'cloudflare:workers'
import type { Env } from './index'
import type { MetricIn, HistogramIn, MetricStats } from './db'

/** A metric key's running aggregate — the materialized `runs.stats[key]`. */
interface KeyStat extends MetricStats {
  lastStep: number // MAX(step), surfaced as metricKeys().last_step
}

/** Everything the run needs to rebuild its rolled-up state after eviction. Held
 * in memory and mirrored to the `state` kv row so a cold DO restores instantly. */
interface RunState {
  runId: string
  seqM: number // highest metric seq durably committed (0 = none)
  seqH: number // highest histogram seq
  summary: Record<string, number> // last value per metric key
  stats: Record<string, KeyStat> // min/max/count/last/lastStep per metric key
  histKeys: Record<string, { count: number; lastStep: number }>
  progress: number | null
  progressTotal: number | null
  progressTs: number | null
  updatedAt: number
  status: string
  finishedAt: number | null
}

const now = () => Date.now() / 1000

function blankState(): RunState {
  return {
    runId: '',
    seqM: 0,
    seqH: 0,
    summary: {},
    stats: {},
    histKeys: {},
    progress: null,
    progressTotal: null,
    progressTs: null,
    updatedAt: now(),
    status: 'running',
    finishedAt: null,
  }
}

/** Keep index positions that survive stride sampling to `target` points; the very
 * last point is always kept. Mirrors db.ts's `(rn-1) % stride == 0 OR rn == total`. */
function sampleIndices(total: number, target: number): number[] {
  const stride = Math.max(1, Math.floor((total + target - 1) / target))
  const keep: number[] = []
  for (let rn = 1; rn <= total; rn++) if ((rn - 1) % stride === 0 || rn === total) keep.push(rn - 1)
  return keep
}

export class RunStore extends DurableObject<Env> {
  private sql: SqlStorage
  private state: RunState

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env)
    this.sql = ctx.storage.sql
    this.state = blankState()
    ctx.blockConcurrencyWhile(async () => {
      this.sql.exec(`CREATE TABLE IF NOT EXISTS segments (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        kind  TEXT NOT NULL,        -- 'm' metric | 'h' histogram
        key   TEXT NOT NULL,
        start_step INTEGER NOT NULL,
        end_step   INTEGER NOT NULL,
        count INTEGER NOT NULL,
        blob  TEXT NOT NULL         -- columnar JSON: {steps,values,ts} or {steps,bins,counts,ts}
      )`)
      this.sql.exec('CREATE INDEX IF NOT EXISTS idx_segments ON segments (kind, key, end_step)')
      this.sql.exec('CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)')
      const row = this.sql.exec<{ v: string }>("SELECT v FROM kv WHERE k = 'state'").toArray()[0]
      if (row) this.state = { ...blankState(), ...JSON.parse(row.v) }
    })
  }

  private persistState() {
    this.sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES ('state', ?)", JSON.stringify(this.state))
  }

  /** Roll the run's freshness/summary/stats into its D1 catalog row. One row write
   * per ingest batch — the same place the old server wrote `touchRun`. */
  private async syncRun() {
    await this.env.DB.prepare(
      `UPDATE runs SET updated_at = ?1, progress = ?2, progress_total = ?3, progress_ts = ?4,
                       summary = ?5, stats = ?6 WHERE id = ?7`,
    )
      .bind(
        this.state.updatedAt,
        this.state.progress,
        this.state.progressTotal,
        this.state.progressTs,
        JSON.stringify(this.state.summary),
        JSON.stringify(this.statsForRun()),
        this.state.runId,
      )
      .run()
  }

  /** stats minus the DO-internal lastStep field — the shape db.runToDict expects. */
  private statsForRun(): Record<string, MetricStats> {
    const out: Record<string, MetricStats> = {}
    for (const [k, s] of Object.entries(this.state.stats)) out[k] = { min: s.min, max: s.max, count: s.count, last: s.last }
    return out
  }

  private touch(ts: number) {
    if (ts > this.state.updatedAt) this.state.updatedAt = ts
  }

  // ----------------------------------------------------------------- ingest

  async ingestMetrics(runId: string, rows: MetricIn[]): Promise<number> {
    this.state.runId = runId
    const seqed = rows.length > 0 && rows.every((r) => r.seq !== null && r.seq !== undefined)
    const fresh = seqed ? rows.filter((r) => (r.seq ?? 0) > this.state.seqM) : rows
    if (fresh.length === 0) return 0
    if (seqed) this.state.seqM = Math.max(this.state.seqM, ...fresh.map((r) => r.seq!))

    // group by key so each key's batch becomes one segment (its own step range)
    const byKey = new Map<string, MetricIn[]>()
    for (const r of fresh) {
      ;(byKey.get(r.key) ?? byKey.set(r.key, []).get(r.key)!).push(r)
      const s = (this.state.stats[r.key] ??= { min: r.value, max: r.value, count: 0, last: r.value, lastStep: r.step })
      s.min = Math.min(s.min, r.value)
      s.max = Math.max(s.max, r.value)
      s.count += 1
      s.last = r.value // rows arrive in client rowid order; last wins
      s.lastStep = Math.max(s.lastStep, r.step)
      this.state.summary[r.key] = r.value
    }
    let maxTs = 0
    for (const [key, pts] of byKey) {
      const steps = pts.map((p) => p.step)
      this.sql.exec(
        'INSERT INTO segments (kind, key, start_step, end_step, count, blob) VALUES (?,?,?,?,?,?)',
        'm',
        key,
        Math.min(...steps),
        Math.max(...steps),
        pts.length,
        JSON.stringify({ steps, values: pts.map((p) => p.value), ts: pts.map((p) => p.ts) }),
      )
      maxTs = Math.max(maxTs, ...pts.map((p) => p.ts))
    }
    this.touch(maxTs)
    this.persistState()
    await this.syncRun()
    return fresh.length
  }

  async ingestHistograms(runId: string, rows: HistogramIn[]): Promise<number> {
    this.state.runId = runId
    const seqed = rows.length > 0 && rows.every((r) => r.seq !== null && r.seq !== undefined)
    const fresh = seqed ? rows.filter((r) => (r.seq ?? 0) > this.state.seqH) : rows
    if (fresh.length === 0) return 0
    if (seqed) this.state.seqH = Math.max(this.state.seqH, ...fresh.map((r) => r.seq!))

    const byKey = new Map<string, HistogramIn[]>()
    for (const r of fresh) {
      ;(byKey.get(r.key) ?? byKey.set(r.key, []).get(r.key)!).push(r)
      const h = (this.state.histKeys[r.key] ??= { count: 0, lastStep: r.step })
      h.count += 1
      h.lastStep = Math.max(h.lastStep, r.step)
    }
    let maxTs = 0
    for (const [key, pts] of byKey) {
      const steps = pts.map((p) => p.step)
      this.sql.exec(
        'INSERT INTO segments (kind, key, start_step, end_step, count, blob) VALUES (?,?,?,?,?,?)',
        'h',
        key,
        Math.min(...steps),
        Math.max(...steps),
        pts.length,
        JSON.stringify({ steps, bins: pts.map((p) => p.bins), counts: pts.map((p) => p.counts), ts: pts.map((p) => p.ts) }),
      )
      maxTs = Math.max(maxTs, ...pts.map((p) => p.ts))
    }
    this.touch(maxTs)
    this.persistState()
    await this.syncRun()
    return fresh.length
  }

  // -------------------------------------------------------------- liveness

  async heartbeat(runId: string, ts: number): Promise<void> {
    this.state.runId = runId
    if (this.state.status !== 'running') return // server clock; matches D1 WHERE status='running'
    this.touch(ts)
    this.persistState()
    await this.syncRun()
  }

  async progress(runId: string, current: number, total: number | null, ts: number): Promise<void> {
    this.state.runId = runId
    if (this.state.status !== 'running') return
    this.state.progress = current
    if (total !== null) this.state.progressTotal = total
    this.state.progressTs = ts
    this.touch(ts)
    this.persistState()
    await this.syncRun()
  }

  async finish(
    runId: string,
    status: string,
    finishedAt: number | null,
    summary: Record<string, number> | null,
    metricMeta: Record<string, unknown> | null,
  ): Promise<void> {
    this.state.runId = runId
    const ts = finishedAt ?? now()
    this.state.status = status
    this.state.finishedAt = ts
    this.touch(ts)
    if (summary) Object.assign(this.state.summary, summary) // author scalars override
    this.persistState()
    // Terminal row write. NULL sentinels via COALESCE keep the existing column:
    //  - metric_meta: skip when empty, so a re-finish can't blank earlier specs.
    //  - summary/stats: skip unless the DO holds data, so finishing a legacy run
    //    (its history is in the D1 metrics table) can't blank its summary.
    const hasData = Object.keys(this.state.stats).length > 0 || (summary != null && Object.keys(summary).length > 0)
    const mm = metricMeta && Object.keys(metricMeta).length > 0 ? JSON.stringify(metricMeta) : null
    const sm = hasData ? JSON.stringify(this.state.summary) : null
    const st = hasData ? JSON.stringify(this.statsForRun()) : null
    await this.env.DB.prepare(
      `UPDATE runs SET status = ?1, finished_at = ?2, updated_at = ?3,
         metric_meta = COALESCE(?4, metric_meta),
         summary = COALESCE(?5, summary),
         stats = COALESCE(?6, stats)
       WHERE id = ?7`,
    )
      .bind(status, ts, ts, mm, sm, st, runId)
      .run()
  }

  // ----------------------------------------------------------------- reads

  metricKeys(): Array<{ key: string; points: number; last_step: number }> {
    return Object.entries(this.state.stats)
      .map(([key, s]) => ({ key, points: s.count, last_step: s.lastStep }))
      .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
  }

  /** Decode a key's segments in step order into flat columnar arrays. */
  private metricPoints(key: string, afterStep: number | null) {
    const cond = afterStep !== null ? 'AND end_step > ?2' : ''
    const rows = this.sql
      .exec<{ blob: string }>(
        `SELECT blob FROM segments WHERE kind = 'm' AND key = ?1 ${cond} ORDER BY start_step, id`,
        ...(afterStep !== null ? [key, afterStep] : [key]),
      )
      .toArray()
    const steps: number[] = []
    const values: number[] = []
    const ts: number[] = []
    for (const r of rows) {
      const seg = JSON.parse(r.blob) as { steps: number[]; values: number[]; ts: number[] }
      for (let i = 0; i < seg.steps.length; i++) {
        if (afterStep !== null && seg.steps[i] <= afterStep) continue
        steps.push(seg.steps[i])
        values.push(seg.values[i])
        ts.push(seg.ts[i])
      }
    }
    return { steps, values, ts }
  }

  metricSeries(key: string, maxPoints: number, afterStep: number | null) {
    const all = this.metricPoints(key, afterStep)
    // incremental tail (live charts): no sampling — the gap between polls is small
    if (afterStep !== null) return all
    const idx = sampleIndices(all.steps.length, Math.max(1, maxPoints))
    return { steps: idx.map((i) => all.steps[i]), values: idx.map((i) => all.values[i]), ts: idx.map((i) => all.ts[i]) }
  }

  histogramKeys(): Array<{ key: string; points: number; last_step: number }> {
    return Object.entries(this.state.histKeys)
      .map(([key, h]) => ({ key, points: h.count, last_step: h.lastStep }))
      .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
  }

  histogramSeries(key: string, maxSteps: number) {
    const rows = this.sql
      .exec<{ blob: string }>("SELECT blob FROM segments WHERE kind = 'h' AND key = ?1 ORDER BY start_step, id", key)
      .toArray()
    const steps: number[] = []
    const bins: number[][] = []
    const counts: number[][] = []
    const ts: number[] = []
    for (const r of rows) {
      const seg = JSON.parse(r.blob) as { steps: number[]; bins: number[][]; counts: number[][]; ts: number[] }
      for (let i = 0; i < seg.steps.length; i++) {
        steps.push(seg.steps[i])
        bins.push(seg.bins[i])
        counts.push(seg.counts[i])
        ts.push(seg.ts[i])
      }
    }
    const idx = sampleIndices(steps.length, Math.max(1, maxSteps))
    return { steps: idx.map((i) => steps[i]), bins: idx.map((i) => bins[i]), counts: idx.map((i) => counts[i]), ts: idx.map((i) => ts[i]) }
  }

  /** Drop the run's whole time series (called from DELETE handlers). */
  async deleteAll(): Promise<void> {
    this.sql.exec('DELETE FROM segments')
    this.sql.exec('DELETE FROM kv')
    this.state = blankState()
  }
}
