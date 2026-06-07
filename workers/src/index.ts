/** pandm cloud server on Cloudflare Workers: D1 (runs/metrics) + R2 (media).
 * Speaks the same HTTP protocol as the Python server (src/pandm/server/) —
 * the SDK, `pandm login` device flow and the dashboard work unchanged.
 * Always multi-user: GitHub OAuth + per-user API keys + per-user isolation. */

import { Hono } from 'hono'
import * as auth from './auth'
import * as db from './db'

export interface Env {
  DB: D1Database
  MEDIA: R2Bucket
  ASSETS: Fetcher
  GITHUB_CLIENT_ID: string
  GITHUB_CLIENT_SECRET: string
  PANDM_SECRET_KEY: string
}

type Ctx = { Bindings: Env; Variables: { user: db.User } }

const app = new Hono<Ctx>()

const detail = (c: any, status: number, msg: string) => c.json({ detail: msg }, status)

// every /api route below this middleware requires an identity:
// x-api-key (SDK / CLI) or the session cookie (dashboard)
app.use('/api/*', async (c, next) => {
  const open = ['/api/auth/login', '/api/auth/callback', '/api/auth/logout', '/api/cli/start', '/api/cli/poll']
  if (open.includes(c.req.path)) return next()
  const apiKey = c.req.header('x-api-key')
  let user: db.User | null = null
  if (apiKey) {
    user = await db.userByApiKey(c.env.DB, apiKey)
  } else {
    const session = await auth.verify(c.env.PANDM_SECRET_KEY, auth.readCookie(c.req.header('Cookie'), auth.SESSION_COOKIE))
    if (session) user = await db.userById(c.env.DB, session.uid)
  }
  if (!user) return detail(c, 401, 'sign in required')
  c.set('user', user)
  return next()
})

/** In multi-user mode a foreign run is indistinguishable from a missing one. */
async function ownerGuard(c: any, runId: string): Promise<Response | null> {
  const owner = await db.runOwner(c.env.DB, runId)
  if (owner !== c.get('user').id) return detail(c, 404, 'run not found')
  return null
}

// ----------------------------------------------------------------- read API

app.get('/api/projects', async (c) => c.json(await db.listProjects(c.env.DB, c.get('user').id)))

app.get('/api/runs', async (c) =>
  c.json(await db.listRuns(c.env.DB, c.get('user').id, c.req.query('project') ?? null)),
)

app.get('/api/runs/:id', async (c) => {
  const run = await db.getRun(c.env.DB, c.req.param('id'), c.get('user').id)
  return run ? c.json(run) : detail(c, 404, 'run not found')
})

app.get('/api/runs/:id/metrics', async (c) => {
  const runId = c.req.param('id')
  return (await ownerGuard(c, runId)) ?? c.json(await db.metricKeys(c.env.DB, runId))
})

// metric keys may contain slashes (e.g. "val/loss") — wildcard + manual extraction
app.get('/api/runs/:id/metrics/*', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const key = decodeURIComponent(c.req.path.split('/metrics/')[1] ?? '')
  const maxPoints = Number.parseInt(c.req.query('max_points') ?? '1500')
  return c.json(await db.metricSeries(c.env.DB, runId, key, maxPoints))
})

app.get('/api/runs/:id/media', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const items = (await db.listMedia(c.env.DB, runId, c.req.query('key') ?? null)) as Array<Record<string, unknown>>
  for (const item of items) item.url = `/api/media/${runId}/${item.filename}`
  return c.json(items)
})

const MIME: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
}

app.get('/api/media/:runId/:filename', async (c) => {
  const { runId, filename } = c.req.param()
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const obj = await c.env.MEDIA.get(`media/${runId}/${filename}`)
  if (!obj) return detail(c, 404, 'file not found')
  const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase()
  return new Response(obj.body, {
    headers: {
      'Content-Type': MIME[ext] ?? 'application/octet-stream',
      'Cache-Control': 'private, max-age=31536000, immutable', // media files never change
    },
  })
})

// --------------------------------------------------------------- ingest API

app.post('/api/runs', async (c) => {
  const body = await c.req.json<{ id?: string; project?: string; name?: string; config?: unknown; created_at?: number }>()
  const runId = body.id || db.newRunId()
  // a re-created id must still be yours, or someone could attach to a foreign run
  const owner = await db.runOwner(c.env.DB, runId)
  if (owner !== null && owner !== c.get('user').id) return detail(c, 404, 'run not found')
  await db.createRun(
    c.env.DB, runId, body.project ?? 'default', body.name ?? 'unnamed',
    body.config ?? {}, body.created_at ?? null, c.get('user').id,
  )
  return c.json({ id: runId })
})

app.post('/api/runs/:id/metrics', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const { rows } = await c.req.json<{ rows: db.MetricIn[] }>()
  let inserted: number
  if (rows.length > 0 && rows.every((r) => r.seq !== null && r.seq !== undefined)) {
    inserted = await db.logMetricsSeq(c.env.DB, runId, rows)
  } else {
    await db.logMetrics(c.env.DB, runId, rows)
    inserted = rows.length
  }
  return c.json({ inserted })
})

app.post('/api/runs/:id/media', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const form = await c.req.formData()
  const entry = form.get('file') as unknown // workers-types lacks File in FormDataEntryValue
  if (!entry || typeof entry !== 'object' || !('stream' in entry)) return detail(c, 422, 'file is required')
  const file = entry as File
  const mediaSeq = form.get('media_seq')
  if (mediaSeq !== null && !(await db.claimMediaSeq(c.env.DB, runId, Number(mediaSeq)))) {
    return c.json({ filename: null, skipped: true }) // replay of an already-ingested upload
  }
  const dot = file.name.lastIndexOf('.')
  const ext = (dot >= 0 ? file.name.slice(dot) : '.png').toLowerCase()
  const ts = form.get('ts') !== null ? Number(form.get('ts')) : Date.now() / 1000
  const filename = await db.logMedia(
    c.env.DB, runId, String(form.get('key') ?? ''), Number(form.get('step') ?? 0), ext,
    String(form.get('caption') ?? '') || null, ts,
  )
  await c.env.MEDIA.put(`media/${runId}/${filename}`, file.stream(), {
    httpMetadata: { contentType: file.type || undefined },
  })
  return c.json({ filename })
})

app.post('/api/runs/:id/heartbeat', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  await db.heartbeat(c.env.DB, runId) // server clock — immune to client clock skew
  return c.json({ ok: true })
})

app.post('/api/runs/:id/finish', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const body = await c.req.json<{ status?: string; finished_at?: number }>()
  await db.finishRun(c.env.DB, runId, body.status ?? 'finished', body.finished_at ?? null)
  return c.json({ status: body.status ?? 'finished' })
})

app.delete('/api/runs/:id', async (c) => {
  const runId = c.req.param('id')
  const guard = await ownerGuard(c, runId)
  if (guard) return guard
  const filenames = await db.deleteRun(c.env.DB, runId)
  if (filenames.length > 0) await c.env.MEDIA.delete(filenames.map((f) => `media/${runId}/${f}`))
  return c.json({ deleted: true })
})

// --------------------------------------------------------------------- auth

app.get('/api/auth/login', async (c) => {
  if (!c.env.GITHUB_CLIENT_ID || !c.env.GITHUB_CLIENT_SECRET || !c.env.PANDM_SECRET_KEY) {
    return detail(c, 500, 'server is missing GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / PANDM_SECRET_KEY secrets')
  }
  const state = crypto.randomUUID().replace(/-/g, '')
  const stateToken = await auth.sign(c.env.PANDM_SECRET_KEY, { state, exp: Date.now() / 1000 + 600 })
  const url = `${auth.GITHUB_AUTHORIZE}?client_id=${encodeURIComponent(c.env.GITHUB_CLIENT_ID)}&state=${state}&scope=read%3Auser`
  return new Response(null, {
    status: 302,
    headers: {
      Location: url,
      'Set-Cookie': `${auth.STATE_COOKIE}=${stateToken}; Max-Age=600; Path=/; HttpOnly; SameSite=Lax; Secure`,
    },
  })
})

app.get('/api/auth/callback', async (c) => {
  const { code, state } = c.req.query()
  const saved = await auth.verify(c.env.PANDM_SECRET_KEY, auth.readCookie(c.req.header('Cookie'), auth.STATE_COOKIE))
  if (!saved || !state || saved.state !== state) return detail(c, 403, 'oauth state mismatch')
  const profile = await auth.exchangeGithubCode(c.env.GITHUB_CLIENT_ID, c.env.GITHUB_CLIENT_SECRET, code ?? '')
  if (!profile) return detail(c, 403, 'github did not grant a token')
  const user = await db.upsertUser(c.env.DB, profile.id, profile.login, profile.name, profile.avatar_url)
  const session = await auth.sign(c.env.PANDM_SECRET_KEY, { uid: user.id, exp: Date.now() / 1000 + auth.SESSION_TTL })
  const headers = new Headers({ Location: '/' })
  headers.append('Set-Cookie', auth.sessionCookie(session, true))
  headers.append('Set-Cookie', `${auth.STATE_COOKIE}=; Max-Age=0; Path=/`)
  return new Response(null, { status: 302, headers })
})

app.post('/api/auth/logout', () => {
  return new Response(null, {
    status: 204,
    headers: { 'Set-Cookie': `${auth.SESSION_COOKIE}=; Max-Age=0; Path=/` },
  })
})

app.get('/api/me', (c) => {
  const user = c.get('user')
  return c.json({
    mode: 'user',
    login: user.login,
    name: user.name,
    avatar_url: user.avatar_url,
    api_key: user.api_key,
  })
})

app.post('/api/me/key/rotate', async (c) =>
  c.json({ api_key: await db.rotateApiKey(c.env.DB, c.get('user').id) }),
)

// ------------------------------------------------------------- device flow

app.post('/api/cli/start', async (c) => {
  const req = await db.deviceStart(c.env.DB)
  return req ? c.json(req) : detail(c, 429, 'too many pending requests')
})

app.post('/api/cli/approve', async (c) => {
  const { code } = await c.req.json<{ code: string }>()
  const ok = await db.deviceApprove(c.env.DB, code ?? '', c.get('user').api_key)
  return ok ? c.json({ ok: true }) : detail(c, 404, 'unknown or expired code')
})

app.post('/api/cli/poll', async (c) => {
  const { device_token } = await c.req.json<{ device_token: string }>()
  const result = await db.devicePoll(c.env.DB, device_token ?? '')
  return result ? c.json(result) : detail(c, 404, 'unknown or expired device token')
})

export default app
