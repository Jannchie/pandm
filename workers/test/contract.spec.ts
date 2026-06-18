/** Contract tests mirroring tests/test_cloud.py's API-level assertions, so the
 * Workers implementation and the Python server keep speaking the same protocol. */

import { SELF, env, fetchMock } from 'cloudflare:test'
import { beforeAll, describe, expect, it } from 'vitest'
import { SESSION_COOKIE, sign } from '../src/auth'
import { upsertUser, type User } from '../src/db'

const BASE = 'https://pandm.test'

let alice: User
let bob: User

const keyOf = (u: User) => ({ 'x-api-key': u.api_key })

async function session(u: User): Promise<string> {
  const token = await sign('test-secret', { uid: u.id, exp: Date.now() / 1000 + 3600 })
  return `${SESSION_COOKIE}=${token}`
}

const api = (path: string, init?: RequestInit) => SELF.fetch(`${BASE}${path}`, init)

const post = (path: string, body: unknown, headers: Record<string, string> = {}) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) })

beforeAll(async () => {
  alice = await upsertUser(env.DB, 1, 'alice', 'Alice', null)
  bob = await upsertUser(env.DB, 2, 'bob', 'Bob', null)
})

describe('auth & isolation', () => {
  it('rejects anonymous reads', async () => {
    expect((await api('/api/runs')).status).toBe(401)
    expect((await api('/api/me')).status).toBe(401)
  })

  it('identifies users via api key and session cookie', async () => {
    const viaKey = await api('/api/me', { headers: keyOf(alice) })
    expect(((await viaKey.json()) as any).login).toBe('alice')
    const viaCookie = await api('/api/me', { headers: { Cookie: await session(alice) } })
    expect(((await viaCookie.json()) as any).login).toBe('alice')
  })

  it('isolates users: foreign runs are 404 everywhere', async () => {
    await post('/api/runs', { id: 'iso00001', project: 'p', name: 'a' }, keyOf(alice))
    const rows = { rows: [{ key: 'loss', step: 0, value: 1.0, ts: 1.0 }] }
    expect((await post('/api/runs/iso00001/metrics', rows, keyOf(alice))).status).toBe(200)

    expect((await api('/api/runs/iso00001', { headers: keyOf(alice) })).status).toBe(200)
    expect((await api('/api/runs/iso00001', { headers: keyOf(bob) })).status).toBe(404)
    expect((await api('/api/runs/iso00001/metrics/loss', { headers: keyOf(bob) })).status).toBe(404)
    expect((await post('/api/runs/iso00001/metrics', rows, keyOf(bob))).status).toBe(404)
    expect((await api('/api/runs/iso00001', { method: 'DELETE', headers: keyOf(bob) })).status).toBe(404)
    const bobRuns = (await (await api('/api/runs', { headers: keyOf(bob) })).json()) as any[]
    expect(bobRuns.find((r) => r.id === 'iso00001')).toBeUndefined()
  })

  it('blocks re-creating someone else’s run id', async () => {
    await post('/api/runs', { id: 'steal001', project: 'p', name: 'a' }, keyOf(alice))
    expect((await post('/api/runs', { id: 'steal001', project: 'p', name: 'b' }, keyOf(bob))).status).toBe(404)
  })

  it('rotates api keys', async () => {
    const carol = await upsertUser(env.DB, 3, 'carol', null, null)
    const resp = await api('/api/me/key/rotate', { method: 'POST', headers: { Cookie: await session(carol) } })
    const { api_key } = (await resp.json()) as any
    expect(api_key).not.toBe(carol.api_key)
    expect((await api('/api/me', { headers: { 'x-api-key': carol.api_key } })).status).toBe(401)
    expect((await api('/api/me', { headers: { 'x-api-key': api_key } })).status).toBe(200)
  })
})

describe('metrics ingest & watermark', () => {
  it('dedupes re-pushed batches by seq watermark (exact-once)', async () => {
    await post('/api/runs', { id: 'wm000001', project: 'p', name: 'wm' }, keyOf(alice))
    const rows = {
      rows: [1, 2, 3].map((i) => ({ key: 'loss', step: i, value: i, ts: i, seq: i })),
    }
    const first = (await (await post('/api/runs/wm000001/metrics', rows, keyOf(alice))).json()) as any
    expect(first.inserted).toBe(3)
    const replay = (await (await post('/api/runs/wm000001/metrics', rows, keyOf(alice))).json()) as any
    expect(replay.inserted).toBe(0)

    const series = (await (await api('/api/runs/wm000001/metrics/loss', { headers: keyOf(alice) })).json()) as any
    expect(series.steps).toEqual([1, 2, 3]) // exactly once
  })

  it('serves slash-containing metric keys and downsamples long series', async () => {
    await post('/api/runs', { id: 'ds000001', project: 'p', name: 'ds' }, keyOf(alice))
    const rows = Array.from({ length: 4000 }, (_, i) => ({ key: 'val/loss', step: i, value: i, ts: i + 1 }))
    for (let i = 0; i < rows.length; i += 500) {
      await post('/api/runs/ds000001/metrics', { rows: rows.slice(i, i + 500) }, keyOf(alice))
    }
    const series = (await (
      await api('/api/runs/ds000001/metrics/val%2Floss?max_points=500', { headers: keyOf(alice) })
    ).json()) as any
    expect(series.steps.length).toBeLessThanOrEqual(502)
    expect(series.steps[0]).toBe(0)
    expect(series.steps.at(-1)).toBe(3999) // last point always kept

    const keys = (await (await api('/api/runs/ds000001/metrics', { headers: keyOf(alice) })).json()) as any
    expect(keys).toEqual([{ key: 'val/loss', points: 4000, last_step: 3999 }])
  })

  it('keeps run summary at the latest value', async () => {
    await post('/api/runs', { id: 'sum00001', project: 'p', name: 'sum' }, keyOf(alice))
    await post(
      '/api/runs/sum00001/metrics',
      { rows: [{ key: 'acc', step: 0, value: 0.5, ts: 1 }, { key: 'acc', step: 1, value: 0.9, ts: 2 }] },
      keyOf(alice),
    )
    const run = (await (await api('/api/runs/sum00001', { headers: keyOf(alice) })).json()) as any
    expect(run.summary.acc).toBe(0.9)
  })

  it('exposes per-key stats on runs (dashboard lists metrics from run.stats)', async () => {
    await post('/api/runs', { id: 'stat0001', project: 'p', name: 'st' }, keyOf(alice))
    await post(
      '/api/runs/stat0001/metrics',
      { rows: [{ key: 'loss', step: 0, value: 1.0, ts: 1 }, { key: 'loss', step: 1, value: 0.2, ts: 2 }] },
      keyOf(alice),
    )
    // both the single-run endpoint and the list endpoint must carry stats
    const run = (await (await api('/api/runs/stat0001', { headers: keyOf(alice) })).json()) as any
    expect(run.stats.loss).toEqual({ min: 0.2, max: 1.0, count: 2, last: 0.2 })
    const list = (await (await api('/api/runs?project=p', { headers: keyOf(alice) })).json()) as any[]
    expect(list.find((r) => r.id === 'stat0001').stats.loss.count).toBe(2)
  })

  it('lists more than 100 runs without exceeding D1 bound-parameter limit', async () => {
    const carol = await upsertUser(env.DB, 3, 'carol', 'Carol', null)
    for (let i = 0; i < 105; i++) {
      const id = `bulk${String(i).padStart(4, '0')}`
      await post('/api/runs', { id, project: 'bulk', name: id }, keyOf(carol))
    }
    const res = await api('/api/runs?project=bulk', { headers: keyOf(carol) })
    expect(res.status).toBe(200)
    expect(((await res.json()) as any[]).length).toBe(105)
  })
})

describe('media via R2', () => {
  async function upload(runId: string, mediaSeq?: number) {
    const form = new FormData()
    form.append('file', new File([new Uint8Array([137, 80, 78, 71])], 'x.png', { type: 'image/png' }))
    form.append('key', 'samples')
    form.append('step', '4')
    form.append('caption', 'hi')
    form.append('ts', '9.0')
    if (mediaSeq !== undefined) form.append('media_seq', String(mediaSeq))
    return api(`/api/runs/${runId}/media`, { method: 'POST', headers: keyOf(alice), body: form })
  }

  it('roundtrips an upload and dedupes replays', async () => {
    await post('/api/runs', { id: 'med00001', project: 'p', name: 'med' }, keyOf(alice))
    const up = (await (await upload('med00001', 1)).json()) as any
    expect(up.filename).toMatch(/^samples_00000004_/)

    const replay = (await (await upload('med00001', 1)).json()) as any
    expect(replay.skipped).toBe(true)

    const items = (await (await api('/api/runs/med00001/media', { headers: keyOf(alice) })).json()) as any[]
    expect(items).toHaveLength(1)
    expect(items[0].caption).toBe('hi')

    const img = await api(items[0].url, { headers: keyOf(alice) })
    expect(img.status).toBe(200)
    expect(img.headers.get('content-type')).toBe('image/png')
    expect(new Uint8Array(await img.arrayBuffer())).toEqual(new Uint8Array([137, 80, 78, 71]))
    // and the owner check applies to media files too
    expect((await api(items[0].url, { headers: keyOf(bob) })).status).toBe(404)
  })

  it('delete removes rows and R2 objects', async () => {
    await post('/api/runs', { id: 'del00001', project: 'p', name: 'del' }, keyOf(alice))
    const { filename } = (await (await upload('del00001')).json()) as any
    expect(await env.MEDIA.get(`media/del00001/${filename}`)).not.toBeNull()

    await api('/api/runs/del00001', { method: 'DELETE', headers: keyOf(alice) })
    expect((await api('/api/runs/del00001', { headers: keyOf(alice) })).status).toBe(404)
    expect(await env.MEDIA.get(`media/del00001/${filename}`)).toBeNull()
  })

  it('deleting a project removes its runs and R2 objects, scoped per user', async () => {
    await post('/api/runs', { id: 'dpa00001', project: 'doomed', name: 'a' }, keyOf(alice))
    await post('/api/runs', { id: 'dpa00002', project: 'doomed', name: 'b' }, keyOf(alice))
    const { filename } = (await (await upload('dpa00001')).json()) as any
    // bob's same-named project must survive alice's delete
    await post('/api/runs', { id: 'dpb00001', project: 'doomed', name: 'b' }, keyOf(bob))

    await api('/api/projects/doomed', { method: 'DELETE', headers: keyOf(alice) })

    expect((await api('/api/runs/dpa00001', { headers: keyOf(alice) })).status).toBe(404)
    expect((await api('/api/runs/dpa00002', { headers: keyOf(alice) })).status).toBe(404)
    expect(await env.MEDIA.get(`media/dpa00001/${filename}`)).toBeNull()
    expect((await api('/api/runs/dpb00001', { headers: keyOf(bob) })).status).toBe(200)
  })
})

describe('github oauth', () => {
  beforeAll(() => {
    fetchMock.activate()
    fetchMock.disableNetConnect()
  })

  it('full login roundtrip with state verification', async () => {
    const login = await api('/api/auth/login', { redirect: 'manual' })
    expect(login.status).toBe(302)
    const location = new URL(login.headers.get('location')!)
    expect(location.hostname).toBe('github.com')
    const state = location.searchParams.get('state')!
    const stateCookie = login.headers.get('set-cookie')!.split(';')[0]

    // wrong state -> rejected
    const bad = await api(`/api/auth/callback?code=c&state=WRONG`, {
      headers: { Cookie: stateCookie },
      redirect: 'manual',
    })
    expect(bad.status).toBe(403)

    fetchMock
      .get('https://github.com')
      .intercept({ method: 'POST', path: '/login/oauth/access_token' })
      .reply(200, { access_token: 'gh-token' }, { headers: { 'Content-Type': 'application/json' } })
    fetchMock
      .get('https://api.github.com')
      .intercept({ path: '/user' })
      .reply(200, { id: 777, login: 'dave', name: 'Dave', avatar_url: null }, { headers: { 'Content-Type': 'application/json' } })

    const cb = await api(`/api/auth/callback?code=c&state=${state}`, {
      headers: { Cookie: stateCookie },
      redirect: 'manual',
    })
    expect(cb.status).toBe(302)
    expect(cb.headers.get('location')).toBe('/')
    const sessionCookie = cb.headers
      .get('set-cookie')!
      .split(',')
      .find((c) => c.includes(SESSION_COOKIE))!
      .split(';')[0]

    const me = (await (await api('/api/me', { headers: { Cookie: sessionCookie } })).json()) as any
    expect(me.login).toBe('dave')
  })
})

describe('device flow (`pandm login`)', () => {
  it('start -> approve -> poll, one-time read', async () => {
    const start = (await (await api('/api/cli/start', { method: 'POST' })).json()) as any
    expect(start.user_code).toMatch(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)

    let poll = await post('/api/cli/poll', { device_token: start.device_token })
    expect((await poll.json()) as any).toEqual({ status: 'pending' })

    // approval requires a signed-in browser
    expect((await post('/api/cli/approve', { code: start.user_code })).status).toBe(401)
    const ok = await post('/api/cli/approve', { code: start.user_code }, { Cookie: await session(alice) })
    expect(ok.status).toBe(200)

    poll = await post('/api/cli/poll', { device_token: start.device_token })
    expect((await poll.json()) as any).toEqual({ status: 'approved', api_key: alice.api_key })
    // one-time read
    expect((await post('/api/cli/poll', { device_token: start.device_token })).status).toBe(404)
    expect((await post('/api/cli/approve', { code: 'ZZZZ-9999' }, keyOf(alice))).status).toBe(404)
  })
})

describe('run lifecycle', () => {
  it('preserves created_at, finish status, and detects stale runs', async () => {
    await post('/api/runs', { id: 'life0001', project: 'p', name: 'life', created_at: 1000.5 }, keyOf(alice))
    let run = (await (await api('/api/runs/life0001', { headers: keyOf(alice) })).json()) as any
    expect(run.created_at).toBe(1000.5)
    expect(run.status).toBe('crashed') // updated_at == 1000.5 is way past STALE_AFTER

    await api('/api/runs/life0001/heartbeat', { method: 'POST', headers: keyOf(alice) })
    run = (await (await api('/api/runs/life0001', { headers: keyOf(alice) })).json()) as any
    expect(run.status).toBe('running') // heartbeat self-heals staleness

    await post('/api/runs/life0001/finish', { status: 'finished', finished_at: 2000.0 }, keyOf(alice))
    run = (await (await api('/api/runs/life0001', { headers: keyOf(alice) })).json()) as any
    expect(run.status).toBe('finished')
    expect(run.finished_at).toBe(2000.0)
  })

  it('attaches per-metric display specs (define_metric) on finish', async () => {
    await post('/api/runs', { id: 'meta0001', project: 'p', name: 'm' }, keyOf(alice))
    // default before any define_metric
    let run = (await (await api('/api/runs/meta0001', { headers: keyOf(alice) })).json()) as any
    expect(run.metric_meta).toEqual({})

    const spec = { win_rate: { min: 0, max: 1, unit: 'percent', goal: 'max', baseline: 0.5 } }
    await post('/api/runs/meta0001/finish', { status: 'finished', metric_meta: spec }, keyOf(alice))
    run = (await (await api('/api/runs/meta0001', { headers: keyOf(alice) })).json()) as any
    expect(run.metric_meta).toEqual(spec)

    // a later finish without specs must not blank the stored ones
    await post('/api/runs/meta0001/finish', { status: 'finished' }, keyOf(alice))
    run = (await (await api('/api/runs/meta0001', { headers: keyOf(alice) })).json()) as any
    expect(run.metric_meta).toEqual(spec)
  })

  it('accepts live metric_meta pushes (define_metric) before finish, and merges', async () => {
    await post('/api/runs', { id: 'meta0002', project: 'p', name: 'm' }, keyOf(alice))
    await post('/api/runs/meta0002/meta', { metric_meta: { win_rate: { min: 0, max: 1, unit: 'percent' } } }, keyOf(alice))
    let run = (await (await api('/api/runs/meta0002', { headers: keyOf(alice) })).json()) as any
    expect(run.status).toBe('running') // visible while alive, not waiting for finish
    expect(run.metric_meta.win_rate).toEqual({ min: 0, max: 1, unit: 'percent' })

    // a second push merges, last write wins per key
    await post('/api/runs/meta0002/meta', { metric_meta: { loss: { goal: 'min' } } }, keyOf(alice))
    run = (await (await api('/api/runs/meta0002', { headers: keyOf(alice) })).json()) as any
    expect(Object.keys(run.metric_meta).sort()).toEqual(['loss', 'win_rate'])
  })
})

describe('summary materialization', () => {
  it('backfills a pre-migration run (summary NULL) on read, then keeps patching', async () => {
    await post('/api/runs', { id: 'bf000001', project: 'bf', name: 'old' }, keyOf(alice))
    await post(
      '/api/runs/bf000001/metrics',
      { rows: [{ key: 'loss', step: 0, value: 3.0, ts: 1 }, { key: 'acc', step: 0, value: 0.1, ts: 1 }] },
      keyOf(alice),
    )
    // simulate a row created before the summary column existed
    await env.DB.prepare("UPDATE runs SET summary = NULL WHERE id = 'bf000001'").run()

    // ingest against a NULL summary must NOT create a partial one (it would shadow 'acc')
    await post('/api/runs/bf000001/metrics', { rows: [{ key: 'loss', step: 1, value: 2.0, ts: 2 }] }, keyOf(alice))
    const nul = await env.DB.prepare("SELECT summary FROM runs WHERE id = 'bf000001'").first<{ summary: string | null }>()
    expect(nul!.summary).toBeNull()

    // first read aggregates the full history and writes it back
    const run = (await (await api('/api/runs/bf000001', { headers: keyOf(alice) })).json()) as any
    expect(run.summary).toEqual({ loss: 2.0, acc: 0.1 })
    const filled = await env.DB.prepare("SELECT summary FROM runs WHERE id = 'bf000001'").first<{ summary: string | null }>()
    expect(JSON.parse(filled!.summary!)).toEqual({ loss: 2.0, acc: 0.1 })

    // subsequent ingest patches the materialized value, older keys survive
    await post('/api/runs/bf000001/metrics', { rows: [{ key: 'loss', step: 2, value: 1.0, ts: 3 }] }, keyOf(alice))
    const list = (await (await api('/api/runs?project=bf', { headers: keyOf(alice) })).json()) as any
    expect(list[0].summary).toEqual({ loss: 1.0, acc: 0.1 })
  })
})

describe('incremental series', () => {
  it('after_step returns only the unsampled tail', async () => {
    await post('/api/runs', { id: 'inc00001', project: 'inc', name: 'inc' }, keyOf(alice))
    const rows = Array.from({ length: 20 }, (_, i) => ({ key: 'loss', step: i, value: i * 1.0, ts: i + 1 }))
    await post('/api/runs/inc00001/metrics', { rows }, keyOf(alice))

    const full = (await (await api('/api/runs/inc00001/metrics/loss', { headers: keyOf(alice) })).json()) as any
    expect(full.steps).toHaveLength(20)

    const tail = (await (
      await api('/api/runs/inc00001/metrics/loss?after_step=16', { headers: keyOf(alice) })
    ).json()) as any
    expect(tail.steps).toEqual([17, 18, 19])
    expect(tail.values).toEqual([17, 18, 19])

    const empty = (await (
      await api('/api/runs/inc00001/metrics/loss?after_step=19', { headers: keyOf(alice) })
    ).json()) as any
    expect(empty.steps).toEqual([])
  })
})
