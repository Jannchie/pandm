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
  status: string
  config: string
  created_at: number
  updated_at: number
  finished_at: number | null
  user_id: number
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

export function runToDict(row: RunRow, summary: Record<string, number> = {}) {
  let status = row.status
  if (status === 'running' && now() - row.updated_at > STALE_AFTER) status = 'crashed'
  return {
    id: row.id,
    project: row.project,
    name: row.name,
    status,
    config: JSON.parse(row.config),
    created_at: row.created_at,
    updated_at: row.updated_at,
    finished_at: row.finished_at,
    user_id: row.user_id,
    summary,
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
): Promise<void> {
  const ts = createdAt ?? now()
  await db
    .prepare(
      `INSERT OR IGNORE INTO runs (id, project, name, status, config, created_at, updated_at, user_id)
       VALUES (?1, ?2, ?3, 'running', ?4, ?5, ?6, ?7)`,
    )
    .bind(runId, project, name, JSON.stringify(config ?? {}), ts, ts, userId)
    .run()
}

export const runOwner = async (db: D1Database, runId: string): Promise<number | null> => {
  const row = await db.prepare('SELECT user_id FROM runs WHERE id = ?1').bind(runId).first<{ user_id: number }>()
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

/** Latest logged value per (run, key) — same MAX(rowid) trick as LocalStore. */
async function summaries(db: D1Database, runIds: string[]): Promise<Record<string, Record<string, number>>> {
  if (runIds.length === 0) return {}
  const placeholders = runIds.map((_, i) => `?${i + 1}`).join(',')
  const { results } = await db
    .prepare(
      `SELECT m.run_id, m.key, m.value FROM metrics m
       JOIN (
         SELECT run_id, key, MAX(rowid) AS mr FROM metrics
         WHERE run_id IN (${placeholders}) GROUP BY run_id, key
       ) t ON m.rowid = t.mr`,
    )
    .bind(...runIds)
    .all<{ run_id: string; key: string; value: number }>()
  const out: Record<string, Record<string, number>> = {}
  for (const r of results) (out[r.run_id] ??= {})[r.key] = r.value
  return out
}

export async function listRuns(db: D1Database, userId: number, project: string | null) {
  const stmt = project
    ? db.prepare('SELECT * FROM runs WHERE user_id = ?1 AND project = ?2 ORDER BY created_at DESC').bind(userId, project)
    : db.prepare('SELECT * FROM runs WHERE user_id = ?1 ORDER BY created_at DESC').bind(userId)
  const { results } = await stmt.all<RunRow>()
  const sums = await summaries(
    db,
    results.map((r) => r.id),
  )
  return results.map((r) => runToDict(r, sums[r.id] ?? {}))
}

export async function getRun(db: D1Database, runId: string, userId: number) {
  const row = await db.prepare('SELECT * FROM runs WHERE id = ?1').bind(runId).first<RunRow>()
  if (!row || row.user_id !== userId) return null // foreign run is indistinguishable from absent
  const sums = await summaries(db, [runId])
  return runToDict(row, sums[runId] ?? {})
}

export async function deleteRun(db: D1Database, runId: string): Promise<string[]> {
  const { results } = await db
    .prepare('SELECT filename FROM media WHERE run_id = ?1')
    .bind(runId)
    .all<{ filename: string }>()
  await db.batch([
    db.prepare('DELETE FROM metrics WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM media WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM sync_progress WHERE run_id = ?1').bind(runId),
    db.prepare('DELETE FROM runs WHERE id = ?1').bind(runId),
  ])
  return results.map((r) => r.filename) // caller removes the R2 objects
}

export const heartbeat = (db: D1Database, runId: string) =>
  db.prepare("UPDATE runs SET updated_at = ?1 WHERE id = ?2 AND status = 'running'").bind(now(), runId).run()

export const finishRun = (db: D1Database, runId: string, status: string, finishedAt: number | null) => {
  const ts = finishedAt ?? now()
  return db
    .prepare('UPDATE runs SET status = ?1, finished_at = ?2, updated_at = ?3 WHERE id = ?4')
    .bind(status, ts, ts, runId)
    .run()
}

// ----------------------------------------------------------------- metrics

/** Plain ingest (legacy PANDM_REMOTE path: no seq, no dedup). */
export async function logMetrics(db: D1Database, runId: string, rows: MetricIn[]): Promise<void> {
  if (rows.length === 0) return
  const stmts = rows.map((r) =>
    db.prepare('INSERT INTO metrics (run_id, key, step, value, ts) VALUES (?1, ?2, ?3, ?4, ?5)').bind(runId, r.key, r.step, r.value, r.ts),
  )
  stmts.push(
    db.prepare('UPDATE runs SET updated_at = ?1 WHERE id = ?2').bind(Math.max(...rows.map((r) => r.ts)), runId),
  )
  await db.batch(stmts)
}

/** Watermarked ingest: rows at or below the stored watermark are replays.
 * The client-side lease serializes pushes per run, so the read-then-write
 * here doesn't need its own lock (same guarantee as the Python server). */
export async function logMetricsSeq(db: D1Database, runId: string, rows: MetricIn[]): Promise<number> {
  const wm = await db
    .prepare('SELECT last_metrics_rowid FROM sync_progress WHERE run_id = ?1')
    .bind(runId)
    .first<{ last_metrics_rowid: number }>()
  const last = wm?.last_metrics_rowid ?? 0
  const fresh = rows.filter((r) => (r.seq ?? 0) > last)
  if (fresh.length === 0) return 0
  const hi = Math.max(...fresh.map((r) => r.seq!))
  const stmts = fresh.map((r) =>
    db.prepare('INSERT INTO metrics (run_id, key, step, value, ts) VALUES (?1, ?2, ?3, ?4, ?5)').bind(runId, r.key, r.step, r.value, r.ts),
  )
  stmts.push(
    db
      .prepare(
        `INSERT INTO sync_progress (run_id, last_metrics_rowid) VALUES (?1, ?2)
         ON CONFLICT(run_id) DO UPDATE SET last_metrics_rowid = MAX(last_metrics_rowid, excluded.last_metrics_rowid)`,
      )
      .bind(runId, hi),
    db.prepare('UPDATE runs SET updated_at = ?1 WHERE id = ?2').bind(Math.max(...fresh.map((r) => r.ts)), runId),
  )
  await db.batch(stmts) // one transaction, one roundtrip
  return fresh.length
}

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

export async function metricSeries(db: D1Database, runId: string, key: string, maxPoints = 1500) {
  const total = await db
    .prepare('SELECT COUNT(*) AS n FROM metrics WHERE run_id = ?1 AND key = ?2')
    .bind(runId, key)
    .first<{ n: number }>()
  if (!total || total.n === 0) return { steps: [], values: [], ts: [] }
  const stride = Math.max(1, Math.ceil(total.n / maxPoints))
  const { results } = await db
    .prepare(
      `SELECT step, value, ts FROM (
         SELECT step, value, ts, ROW_NUMBER() OVER (ORDER BY step, rowid) AS rn
         FROM metrics WHERE run_id = ?1 AND key = ?2
       ) WHERE (rn - 1) % ?3 = 0 OR rn = ?4`,
    )
    .bind(runId, key, stride, total.n)
    .all<{ step: number; value: number; ts: number }>()
  return {
    steps: results.map((r) => r.step),
    values: results.map((r) => r.value),
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
