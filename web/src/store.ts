import { computed, reactive, watchEffect } from 'vue'
import * as api from './api'

export const state = reactive({
  auth: {
    mode: 'loading' as 'loading' | 'local' | 'anon' | 'user',
    user: null as null | { login: string; name: string | null; avatar_url: string | null; api_key: string },
  },
  cliCode: null as string | null, // pending `pandm login` approval code (?cli=)
  ready: false,
  offline: false,
  live: true, // auto-refresh switch; off = data frozen until toggled back
  projects: [] as api.Project[],
  project: '' as string, // auto-picked on first load (most recently active)
  runs: [] as api.Run[],
  selected: [] as string[],
  touched: false, // user has manually changed the selection
  search: '',
  sidebarOpen: false, // mobile drawer; ignored on md+ where the sidebar is static
  tab: 'metrics' as 'metrics' | 'media' | 'table',
  columns: 0, // grid columns for metrics/media, 0 = auto
  smoothing: 0,
  xAxis: 'step' as 'step' | 'time',
  logScale: false,
  expandedChart: null as string | null,
  lightbox: null as null | { url: string; title: string; sub: string },
})

export const visibleRuns = computed(() => {
  const q = state.search.trim().toLowerCase()
  if (!q) return state.runs
  return state.runs.filter(
    (r) => r.name.toLowerCase().includes(q) || r.id.includes(q) || r.project.toLowerCase().includes(q),
  )
})

export const selectedRuns = computed(() => state.runs.filter((r) => state.selected.includes(r.id)))

export const anyRunning = computed(() => selectedRuns.value.some((r) => r.status === 'running'))

export function toggleRun(id: string) {
  state.touched = true
  const idx = state.selected.indexOf(id)
  if (idx >= 0) state.selected.splice(idx, 1)
  else state.selected.push(id)
}

export function selectAll() {
  state.touched = true
  state.selected = visibleRuns.value.map((r) => r.id)
}

export function selectNone() {
  state.touched = true
  state.selected = []
}

export async function setProject(project: string) {
  state.project = project
  await refresh()
}

export async function removeRun(id: string) {
  await api.deleteRun(id)
  state.runs = state.runs.filter((r) => r.id !== id)
  state.selected = state.selected.filter((s) => s !== id)
  refresh()
}

export async function refresh() {
  try {
    const [projects, runs] = await Promise.all([api.fetchProjects(), api.fetchRuns(state.project || undefined)])
    state.projects = projects
    // there is no all-projects view — fall back to the most recently active project
    if (!projects.some((p) => p.project === state.project)) {
      state.project = projects[0]?.project ?? ''
      state.runs = state.project ? await api.fetchRuns(state.project) : runs
    } else {
      state.runs = runs
    }
    state.offline = false
    if (!state.ready) {
      // first load: pre-select the most recent runs so the page isn't empty
      state.selected = runs.slice(0, 4).map((r) => r.id)
      state.ready = true
    }
  } catch (err) {
    if (err instanceof api.HttpError && err.status === 401) {
      // session expired mid-flight — back to the login gate
      state.auth.mode = 'anon'
      polling = false
      clearTimeout(timer)
      return
    }
    state.offline = true
  }
}

let timer: ReturnType<typeof setTimeout> | undefined
let pollMs = 2500
let polling = false

const IDLE_POLL_MS = 15_000 // nothing running -> nothing new to fetch, poll lazily

export function startPolling(intervalMs = 2500) {
  pollMs = intervalMs
  polling = true
  resumePolling()
}

function resumePolling() {
  clearTimeout(timer)
  refresh().then(schedule)
}

function schedule() {
  if (!polling || !state.live) return
  clearTimeout(timer)
  const delay = state.runs.some((r) => r.status === 'running') ? pollMs : IDLE_POLL_MS
  timer = setTimeout(() => refresh().then(schedule), delay)
}

export function toggleLive() {
  state.live = !state.live
  clearTimeout(timer)
  if (state.live && polling) resumePolling()
}

// a backgrounded tab would otherwise poll (and bill D1 reads) all day
document.addEventListener('visibilitychange', () => {
  if (!polling || !state.live) return
  clearTimeout(timer)
  if (document.visibilityState === 'visible') resumePolling()
})

export async function bootstrap() {
  try {
    const me = await api.fetchMe()
    if (me.mode === 'user') {
      state.auth.mode = 'user'
      state.auth.user = {
        login: me.login!,
        name: me.name ?? null,
        avatar_url: me.avatar_url ?? null,
        api_key: me.api_key!,
      }
    } else {
      state.auth.mode = 'local'
      state.cliCode = null // device flow is meaningless without accounts
    }
  } catch (err) {
    if (err instanceof api.HttpError && err.status === 401) {
      state.auth.mode = 'anon'
      return // gate shows; polling starts after sign-in reloads the page
    }
    state.auth.mode = 'local' // server unreachable: fall through, refresh() will mark offline
  }
  startPolling()
}

export async function signOut() {
  await api.logout().catch(() => {})
  polling = false
  clearTimeout(timer)
  state.auth.mode = 'anon'
  state.auth.user = null
}

// ------------------------------------------------------------ preferences
// view settings survive reloads; URL params (below) still win for deep links

const PREFS_KEY = 'pandm-prefs'
const PREF_FIELDS = ['tab', 'columns', 'smoothing', 'xAxis', 'logScale'] as const

try {
  const saved = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')
  for (const f of PREF_FIELDS) {
    if (f in saved && typeof saved[f] === typeof state[f]) (state as any)[f] = saved[f]
  }
} catch {
  /* corrupted prefs are simply ignored */
}

watchEffect(() => {
  localStorage.setItem(PREFS_KEY, JSON.stringify(Object.fromEntries(PREF_FIELDS.map((f) => [f, state[f]]))))
})

// ------------------------------------------------------------- deep links
// tab / project / selected runs live in the query string, so views are shareable

{
  const params = new URLSearchParams(location.search)
  const tab = params.get('tab')
  if (tab === 'metrics' || tab === 'media' || tab === 'table') state.tab = tab
  const project = params.get('project')
  if (project) state.project = project
  const runs = params.get('runs')?.split(',').filter(Boolean)
  if (runs?.length) {
    state.selected = runs
    state.touched = true
  }
  const smooth = Number.parseFloat(params.get('smooth') ?? '')
  if (!Number.isNaN(smooth)) state.smoothing = Math.min(0.99, Math.max(0, smooth))
  const cols = Number.parseInt(params.get('cols') ?? '')
  if (!Number.isNaN(cols)) state.columns = Math.min(6, Math.max(0, cols))
  // `pandm login` approval code — stash it so it survives the GitHub OAuth roundtrip
  const cli = params.get('cli')
  if (cli) sessionStorage.setItem('pandm_cli', cli)
  state.cliCode = cli ?? sessionStorage.getItem('pandm_cli')
}

export function dismissCli() {
  state.cliCode = null
  sessionStorage.removeItem('pandm_cli')
}

watchEffect(() => {
  const params = new URLSearchParams()
  if (state.project) params.set('project', state.project)
  if (state.tab !== 'metrics') params.set('tab', state.tab)
  if (state.touched && state.selected.length) params.set('runs', state.selected.join(','))
  if (state.smoothing > 0) params.set('smooth', String(state.smoothing))
  if (state.columns > 0) params.set('cols', String(state.columns))
  const qs = params.toString()
  history.replaceState(null, '', qs ? `?${qs}` : location.pathname)
})

// ---------------------------------------------------------------- caches
// keyed by run id, invalidated whenever the run's updated_at changes

interface CacheEntry<T> {
  updated: number
  promise: Promise<T>
}

function cached<T>(map: Map<string, CacheEntry<T>>, run: api.Run, key: string, fetcher: () => Promise<T>): Promise<T> {
  const hit = map.get(key)
  if (hit && hit.updated === run.updated_at) return hit.promise
  const promise = fetcher().catch((err) => {
    map.delete(key) // don't cache failures
    throw err
  })
  map.set(key, { updated: run.updated_at, promise })
  return promise
}

const seriesCache = new Map<string, CacheEntry<api.Series>>()
const mediaCache = new Map<string, CacheEntry<api.MediaItem[]>>()

// past this many client-side points, reset to a full (server-sampled) fetch
const MAX_CLIENT_POINTS = 6000

export function getSeries(run: api.Run, key: string): Promise<api.Series> {
  const ck = `${run.id} ${key}`
  const hit = seriesCache.get(ck)
  if (hit && hit.updated === run.updated_at) return hit.promise
  const promise = (async () => {
    // live charts append the tail (an index range read server-side) instead of
    // re-reading the whole series on every poll. Late out-of-order steps are
    // missed until the next full fetch — acceptable for a live view.
    const prev = hit ? await hit.promise.catch(() => null) : null
    if (prev && prev.steps.length > 0 && prev.steps.length < MAX_CLIENT_POINTS) {
      const tail = await api.fetchSeries(run.id, key, prev.steps[prev.steps.length - 1])
      return {
        steps: [...prev.steps, ...tail.steps],
        values: [...prev.values, ...tail.values],
        ts: [...prev.ts, ...tail.ts],
      }
    }
    return api.fetchSeries(run.id, key)
  })()
  promise.catch(() => seriesCache.delete(ck)) // don't cache failures
  seriesCache.set(ck, { updated: run.updated_at, promise })
  return promise
}

export const getMedia = (run: api.Run) => cached(mediaCache, run, run.id, () => api.fetchMedia(run.id))
