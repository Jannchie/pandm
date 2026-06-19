/** D1 data layer — ports the queries from src/pandm/storage.py. */

export interface User {
  id: number
  github_id: number
  login: string
  name: string | null
  avatar_url: string | null
  api_key: string
  created_at: number
}

export interface RunRow {
  id: string
  project: string
  name: string
  description: string // one-line human note (init(description=...))
  status: string
  config: string
  created_at: number
  updated_at: number
  finished_at: number | null
  user_id: number
  progress: number | null // current step/epoch/sample, for ETA
  progress_total: number | null // target; NULL = unknown
  progress_ts: number | null // when progress was last reported
  summary: string | null // materialized {key: lastValue}; NULL = pre-migration row
  stats: string | null // materialized {key:{min,max,count,last}} by the DO; NULL = legacy run
  metric_meta: string // author-declared {key: {min,max,unit,goal,baseline}} display specs
}

export interface MetricIn {
  key: string
  step: number
  value: number
  ts: number
  seq?: number | null
}

/** A 'running' run whose heartbeat stopped this long ago is presumed crashed. */
const STALE_AFTER = 60.0

const now = () => Date.now() / 1000

export interface MetricStats {
  min: number
  max: number
  count: number
  last: number | null
}

export function runToDict(
  row: RunRow,
  summary: Record<string, number> = {},
  stats: Record<string, MetricStats> = {},
) {
  let status = row.status
  if (status === 'running' && now() - row.updated_at > STALE_AFTER) status = 'crashed'
  return {
    id: row.id,
    project: row.project,
    name: row.name,
    description: row.description ?? '',
    status,
    config: JSON.parse(row.config),
    created_at: row.created_at,
    updated_at: row.updated_at,
    finished_at: row.finished_at,
    user_id: row.user_id,
    progress: row.progress,
    progress_total: row.progress_total,
    progress_ts: row.progress_ts,
    summary,
    stats,
    metric_meta: JSON.parse(row.metric_meta ?? '{}'),
  }
}

export const newRunId = () => crypto.randomUUID().replace(/-/g, '').slice(0, 8)

const slug = (text: string) => text.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'x'

// ------------------------------------------------------------------- users

export async function upsertUser(
  db: D1Database,
  githubId: number,
  login: string,
  name: string | null,
  avatarUrl: string | null,
): Promise<User> {
  const apiKey = crypto.randomUUID().replace(/-/g, '') + crypto.randomUUID().replace(/-/g, '')
  await db
    .prepare(
      `INSERT INTO users (github_id, login, name, avatar_url, api_key, created_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6)
       ON CONFLICT(github_id) DO UPDATE SET
         login = excluded.login, name = excluded.name, avatar_url = excluded.avatar_url`,
    )
    .bind(githubId, login, name, avatarUrl, apiKey, now())
    .run()
  const user = await db.prepare('SELECT * FROM users WHERE github_id = ?1').bind(githubId).first<User>()
  return user!
}

export const userById = (db: D1Database, id: number) =>
  db.prepare('SELECT * FROM users WHERE id = ?1').bind(id).first<User>()

export const userByApiKey = (db: D1Database, key: string) =>
  db.prepare('SELECT * FROM users WHERE api_key = ?1').bind(key).first<User>()

export async function rotateApiKey(db: D1Database, userId: number): Promise<string> {
  const key = crypto.randomUUID().replace(/-/g, '') + crypto.randomUUID().replace(/-/g, '')
  await db.prepare('UPDATE users SET api_key = ?1 WHERE id = ?2').bind(key, userId).run()
  return key
}

// -------------------------------------------------------------------- runs

export async function createRun(
  db: D1Database,
  runId: string,
  project: string,
  name: string,
  config: unknown,
  createdAt: number | null,
  userId: number,
  description = '',
): Promise<void> {
  const ts = createdAt ?? now()
  // stats = '{}' (not NULL) marks this run as DO-served — its series live in the
  // RunStore Durable Object, and listRuns/getRun read the materialized stats here.
  await db
    .prepare(
      `INSERT OR IGNORE INTO runs (id, project, name, description, status, config, created_at, updated_at, user_id, summary, stats)
       VALUES (?1, ?2, ?3, ?4, 'running', ?5, ?6, ?7, ?8, '{}', '{}')`,
    )
    .bind(runId, project, name, description, JSON.stringify(config ?? {}), ts, ts, userId)
    .run()
}

export interface RunMeta {
  user_id: number
  updated_at: number
  legacy: number // 1 = pre-DO run (series in D1); 0 = DO-served. Routes reads.
}

/** Owner + freshness + engine in one row read — the cache key for run-scoped
 * responses and the legacy/DO routing flag. */
export const runMeta = async (db: D1Database, runId: string): Promise<RunMeta | null> =>
  db
    .prepare('SELECT user_id, updated_at, (stats IS NULL) AS legacy FROM runs WHERE id = ?1')
    .bind(runId)
    .first<RunMeta>()

export const runOwner = async (db: D1Database, runId: string): Promise<number | null> => {
  const row = await runMeta(db, runId)
  return row?.user_id ?? null
}

export async function listProjects(db: D1Database, userId: number) {
  const { results } = await db
    .prepare(
      `SELECT project, COUNT(*) AS runs, MAX(updated_at) AS last_active
       FROM runs WHERE user_id = ?1 GROUP BY project ORDER BY last_active DESC`,
    )
    .bind(userId)
    .all()
  return results
}

/** D1 caps bound parameters at 100 per query, so IN(...) id lists must be chunked. */
const D1_MAX_PARAMS = 100
const chunk = <T>(arr: T[], size: number): T[][] => {
  const out: T[][] = []
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size))
  return out
}

/** Latest logged value per (run, key) — same MAX(rowid) trick as LocalStore. */
async function summaries(db: D1Database, runIds: string[]): Promise<Record<string, Record<string, number>>> {
  const out: Record<string, Record<string, number>> = {}
  for (const batch of chunk(runIds, D1_MAX_PARAMS)) {
    const placeholders = batch.map((_, i) => `?${i + 1}`).join(',')
    const { results } = await db
      .prepare(
        `SELECT m.run_id, m.key, m.value FROM metrics m
         JOIN (
           SELECT run_id, key, MAX(rowid) AS mr FROM metrics
           WHERE run_id IN (${placeholders}) GROUP BY run_id, key
         ) t ON m.rowid = t.mr`,
      )
      .bind(...batch)
      .all<{ run_id: string; key: string; value: number }>()
    for (const r of results) (out[r.run_id] ??= {})[r.key] = r.value
  }
  return out
}

/** Per-(run, key) aggregates min/max/count + last (latest logged value). Mirrors
 *  LocalStore._stats so the dashboard's MetricsPanel can list a run's metric keys
 *  (it reads `run.stats`). `last` comes from the latest-value `summaries` query. */
async function metricStats(
  db: D1Database,
  runIds: string[],
  last: Record<string, Record<string, number>>,
): Promise<Record<string, Record<string, MetricStats>>> {
  const out: Record<string, Record<string, MetricStats>> = {}
  for (const batch of chunk(runIds, D1_MAX_PARAMS)) {
    const placeholders = batch.map((_, i) => `?${i + 1}`).join(',')
    const { results } = await db
      .prepare(
        `SELECT run_id, key, MIN(value) AS min, MAX(value) AS max, COUNT(*) AS count
         FROM metrics WHERE run_id IN (${placeholders}) GROUP BY run_id, key`,
      )
      .bind(...batch)
      .all<{ run_id: string; key: string; min: number; max: number; count: number }>()
    for (const r of results) {
      ;(out[r.run_id] ??= {})[r.key] = {
        min: r.min,
        max: r.max,
        count: r.count,
        last: last[r.run_id]?.[r.key] ?? null,
      }
    }
  }
  return out
}

/** Aggregate + write back the summaries of pre-migration rows (once per run). */
async function backfillSummaries(db: D1Database, rows: RunRow[]): Promise<Record<string, Record<string, number>>> {
  const missing = rows.filter((r) => r.summary === null)
  if (missing.length === 0) return {}
  const sums = await summaries(
    db,
    missing.map((r) => r.id),
  )
  await db.batch(
    missing.map((r) =>
      db
        .prepare('UPDATE runs SET summary = ?1 WHERE id = ?2 AND summary IS NULL')
        .bind(JSON.stringify(sums[r.id] ?? {}), r.id),
    ),
  )
  return sums
}

export async function listRuns(db: D1Database, userId: number, project: string | null) {
  const stmt = project
    ? db.prepare('SELECT * FROM runs WHERE user_id = ?1 AND project = ?2 ORDER BY created_at DESC').bind(userId, project)
    : db.prepare('SELECT * FROM runs WHERE user_id = ?1 ORDER BY created_at DESC').bind(userId)
  const { results } = await stmt.all<RunRow>()
  // DO-served runs carry their summary + stats materialized on this row — no scan.
  // Only pre-DO ("legacy", stats IS NULL) runs still aggregate over the metrics table.
  const legacy = results.filter((r) => r.stats === null)
  const backfilled = await backfillSummaries(db, legacy)
  const ids = legacy.map((r) => r.id)
  const last = await summaries(db, ids)
  const stats = await metricStats(db, ids, last)
  return results.map((r) =>
    r.stats !== null
      ? runToDict(r, JSON.parse(r.summary ?? '{}'), JSON.parse(r.stats))
      : runToDict(r, r.summary !== null ? JSON.parse(r.summary) : (backfilled[r.id] ?? {}), stats[r.id] ?? {}),
  )
}

export async function getRun(db: D1Database, runId: string, userId: number) {
  const row = await db.prepare('SELECT * FROM runs WHERE id = ?1').bind(runId).first<RunRow>()
  if (!row || row.user_id !== userId) return null // foreign run is indistinguishable from absent
  if (row.stats !== null) return runToDict(row, JSON.parse(row.summary ?? '{}'), JSON.parse(row.stats)) // DO-served
  const backfilled = await backfillSummaries(db, [row]) // legacy fallback
  const last = await summaries(db, [runId])
  const stats = await metricStats(db, [runId], last)
  return runToDict(row, row.summary !== null ? JSON.parse(row.summary) : (backfilled[runId] ?? {}), stats[runId] ?? {})
}

export async function deleteRun(db: D1Database, runId: string): Promise<string[]> {
  const { results } = await db
    .prepare('SELECT filename FROM media WHERE run_id = ?1')
    .bind(runId)
    .all<{ filename: string }>()
  await db.batch([
    db.prepare('DELETE FROM metrics WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM histograms WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM media WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM sync_progress WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM runs WHERE id = ?1').bind(runId),
  ])
  return results.map((r) => r.filename) // caller removes the R2 objects
}

/** Delete every run in a project (and their children). Returns the R2 media keys to
 * remove and the run ids (the caller also drops each run's RunStore DO). */
export async function deleteProject(
  db: D1Database,
  userId: number,
  project: string,
): Promise<{ mediaKeys: string[]; runIds: string[] }> {
  const { results: runs } = await db
    .prepare('SELECT id FROM runs WHERE user_id = ?1 AND project = ?2')
    .bind(userId, project)
    .all<{ id: string }>()
  if (runs.length === 0) return { mediaKeys: [], runIds: [] }
  const runIds = runs.map((r) => r.id)
  const placeholders = runIds.map((_, i) => `?${i + 1}`).join(',')
  const { results: media } = await db
    .prepare(`SELECT run_id, filename FROM media WHERE run_id IN (${placeholders})`)
    .bind(...runIds)
    .all<{ run_id: string; filename: string }>()
  await db.batch([
    db.prepare(`DELETE FROM metrics WHERE run_id IN (${placeholders})`).bind(...runIds),
    db.prepare(`DELETE FROM histograms WHERE run_id IN (${placeholders})`).bind(...runIds),
    db.prepare(`DELETE FROM media WHERE run_id IN (${placeholders})`).bind(...runIds),
    db.prepare(`DELETE FROM sync_progress WHERE run_id IN (${placeholders})`).bind(...runIds),
    db.prepare('DELETE FROM runs WHERE user_id = ?1 AND project = ?2').bind(userId, project),
  ])
  return { mediaKeys: media.map((m) => `media/${m.run_id}/${m.filename}`), runIds }
}

// heartbeat / progress / finish now live in the RunStore DO (run_store.ts):
// the DO owns the run's liveness and writes it through to this catalog row.

/** Merge per-metric display specs into runs.metric_meta — run.define_metric pushed
 *  live (like progress), so a running run's fixed axis/baseline show up right away. */
export const setMetricMeta = (db: D1Database, runId: string, specs: Record<string, unknown>) =>
  db.prepare('UPDATE runs SET metric_meta = json_patch(metric_meta, ?1) WHERE id = ?2').bind(JSON.stringify(specs), runId).run()

// -------------------------------------------------- metrics (legacy reads)
// Ingest moved to the RunStore DO. The query helpers below remain only as the
// read fallback for pre-DO runs whose series still live in the D1 metrics table
// (runMeta().legacy === 1). New runs never touch them.

export const metricKeys = async (db: D1Database, runId: string) => {
  const { results } = await db
    .prepare(
      `SELECT key, COUNT(*) AS points, MAX(step) AS last_step
       FROM metrics WHERE run_id = ?1 GROUP BY key ORDER BY key`,
    )
    .bind(runId)
    .all()
  return results
}

export async function metricSeries(
  db: D1Database,
  runId: string,
  key: string,
  maxPoints = 1500,
  afterStep: number | null = null,
) {
  if (afterStep !== null) {
    // incremental tail for live charts: (run_id, key, step) index range scan
    // reads only the new rows instead of the whole history. No sampling —
    // the tail between two polls is small; the client resets when it grows.
    const { results } = await db
      .prepare(
        'SELECT step, value, ts FROM metrics WHERE run_id = ?1 AND key = ?2 AND step > ?3 ORDER BY step, rowid',
      )
      .bind(runId, key, afterStep)
      .all<{ step: number; value: number; ts: number }>()
    return {
      steps: results.map((r) => r.step),
      values: results.map((r) => r.value),
      ts: results.map((r) => r.ts),
    }
  }
  // COUNT(*) OVER () folds the row count into the same scan — half the rows read
  const { results } = await db
    .prepare(
      `SELECT step, value, ts FROM (
         SELECT step, value, ts,
                ROW_NUMBER() OVER (ORDER BY step, rowid) AS rn,
                COUNT(*) OVER () AS total
         FROM metrics WHERE run_id = ?1 AND key = ?2
       ) WHERE (rn - 1) % MAX(1, (total + ?3 - 1) / ?3) = 0 OR rn = total`,
    )
    .bind(runId, key, Math.max(1, maxPoints))
    .all<{ step: number; value: number; ts: number }>()
  return {
    steps: results.map((r) => r.step),
    values: results.map((r) => r.value),
    ts: results.map((r) => r.ts),
  }
}

// --------------------------------------------------------------- histograms

export interface HistogramIn {
  key: string
  step: number
  bins: number[] // n+1 edges
  counts: number[] // n counts
  ts: number
  seq?: number | null
}

// histogram ingest also moved to the RunStore DO; the reads below stay for the
// legacy fallback only.

export const histogramKeys = async (db: D1Database, runId: string) => {
  const { results } = await db
    .prepare(
      `SELECT key, COUNT(*) AS points, MAX(step) AS last_step
       FROM histograms WHERE run_id = ?1 GROUP BY key ORDER BY key`,
    )
    .bind(runId)
    .all()
  return results
}

export async function histogramSeries(db: D1Database, runId: string, key: string, maxSteps = 200) {
  // same stride-sampling trick as metricSeries — fold the row count into one scan
  const { results } = await db
    .prepare(
      `SELECT step, bins, counts, ts FROM (
         SELECT step, bins, counts, ts,
                ROW_NUMBER() OVER (ORDER BY step, rowid) AS rn,
                COUNT(*) OVER () AS total
         FROM histograms WHERE run_id = ?1 AND key = ?2
       ) WHERE (rn - 1) % MAX(1, (total + ?3 - 1) / ?3) = 0 OR rn = total`,
    )
    .bind(runId, key, Math.max(1, maxSteps))
    .all<{ step: number, bins: string, counts: string, ts: number }>()
  return {
    steps: results.map((r) => r.step),
    bins: results.map((r) => JSON.parse(r.bins)),
    counts: results.map((r) => JSON.parse(r.counts)),
    ts: results.map((r) => r.ts),
  }
}

// ------------------------------------------------------------------- media

export async function claimMediaSeq(db: D1Database, runId: string, mediaId: number): Promise<boolean> {
  const wm = await db
    .prepare('SELECT last_media_id FROM sync_progress WHERE run_id = ?1')
    .bind(runId)
    .first<{ last_media_id: number }>()
  if ((wm?.last_media_id ?? 0) >= mediaId) return false
  await db
    .prepare(
      `INSERT INTO sync_progress (run_id, last_media_id) VALUES (?1, ?2)
       ON CONFLICT(run_id) DO UPDATE SET last_media_id = MAX(last_media_id, excluded.last_media_id)`,
    )
    .bind(runId, mediaId)
    .run()
  return true
}

export async function logMedia(
  db: D1Database,
  runId: string,
  key: string,
  step: number,
  ext: string,
  caption: string | null,
  ts: number,
): Promise<string> {
  const filename = `${slug(key)}_${String(step).padStart(8, '0')}_${crypto.randomUUID().slice(0, 6)}${ext}`
  await db.batch([
    db
      .prepare('INSERT INTO media (run_id, key, step, filename, caption, ts) VALUES (?1, ?2, ?3, ?4, ?5, ?6)')
      .bind(runId, key, step, filename, caption, ts),
    db.prepare('UPDATE runs SET updated_at = ?1 WHERE id = ?2').bind(ts, runId),
  ])
  return filename
}

export async function listMedia(db: D1Database, runId: string, key: string | null) {
  const stmt = key
    ? db.prepare('SELECT key, step, filename, caption, ts FROM media WHERE run_id = ?1 AND key = ?2 ORDER BY key, step, id').bind(runId, key)
    : db.prepare('SELECT key, step, filename, caption, ts FROM media WHERE run_id = ?1 ORDER BY key, step, id').bind(runId)
  const { results } = await stmt.all()
  return results
}

// ------------------------------------------------------------- device flow

const DEVICE_TTL = 600.0

export async function deviceStart(db: D1Database): Promise<{ user_code: string; device_token: string } | null> {
  await db.prepare('DELETE FROM cli_auth WHERE created_at < ?1').bind(now() - DEVICE_TTL).run()
  const pending = await db.prepare('SELECT COUNT(*) AS n FROM cli_auth').first<{ n: number }>()
  if ((pending?.n ?? 0) >= 100) return null // flood guard
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
  const rand = crypto.getRandomValues(new Uint8Array(8))
  const chars = [...rand].map((b) => alphabet[b % alphabet.length])
  const userCode = `${chars.slice(0, 4).join('')}-${chars.slice(4).join('')}`
  const deviceToken = crypto.randomUUID().replace(/-/g, '') + crypto.randomUUID().replace(/-/g, '')
  await db
    .prepare('INSERT INTO cli_auth (user_code, device_token, created_at) VALUES (?1, ?2, ?3)')
    .bind(userCode, deviceToken, now())
    .run()
  return { user_code: userCode, device_token: deviceToken }
}

export async function deviceApprove(db: D1Database, code: string, apiKey: string): Promise<boolean> {
  const res = await db
    .prepare('UPDATE cli_auth SET api_key = ?1 WHERE user_code = ?2 AND created_at >= ?3')
    .bind(apiKey, code.trim().toUpperCase(), now() - DEVICE_TTL)
    .run()
  return res.meta.changes > 0
}

export async function devicePoll(
  db: D1Database,
  deviceToken: string,
): Promise<{ status: 'pending' } | { status: 'approved'; api_key: string } | null> {
  const row = await db
    .prepare('SELECT user_code, api_key FROM cli_auth WHERE device_token = ?1 AND created_at >= ?2')
    .bind(deviceToken, now() - DEVICE_TTL)
    .first<{ user_code: string; api_key: string | null }>()
  if (!row) return null
  if (row.api_key === null) return { status: 'pending' }
  await db.prepare('DELETE FROM cli_auth WHERE user_code = ?1').bind(row.user_code).run() // one-time read
  return { status: 'approved', api_key: row.api_key }
}
