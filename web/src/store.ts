import { computed, reactive, watch, watchEffect } from 'vue'
import * as api from './api'
import { clearEta } from './eta'

export const state = reactive({
  auth: {
    mode: 'loading' as 'loading' | 'local' | 'anon' | 'key' | 'user',
    user: null as null | {
      login: string
      name: string | null
      avatar_url: string | null
      api_key: string
    },
  },
  cliCode: null as string | null, // pending `pandm login` approval code (?cli=)
  ready: false,
  offline: false,
  live: true, // auto-refresh switch; off = data frozen until toggled back
  projects: [] as api.Project[],
  project: '' as string, // auto-picked on first load (most recently active)
  runs: [] as api.Run[],
  selected: [] as string[], // compare set: which runs the charts plot
  marked: [] as string[], // transient marquee/bulk-action set (delete target)
  touched: false, // user has manually changed the selection
  search: '',
  sidebarOpen: false, // mobile drawer; ignored on md+ where the sidebar is static
  sidebarWidth: 280, // desktop sidebar width in px, drag-resizable
  tab: 'metrics' as 'metrics' | 'media' | 'table',
  columns: 0, // grid columns for metrics/media, 0 = auto
  mediaSteps: [] as number[], // union of logged media steps, kept fresh by MediaPanel
  mediaIdx: 0, // index into mediaSteps, driven by the toolbar step slider
  smoothing: 0,
  xAxis: 'step' as 'step' | 'time' | 'rtime', // rtime = elapsed since each run's start
  xRange: null as [number, number] | null, // shared x zoom (units follow xAxis); null = full range
  logScale: false,
  expandedChart: null as string | null,
  // fullscreen media viewer: the full step series for one (run, key) plus the
  // index in view, so the modal can slide across steps without reopening
  lightbox: null as null | {
    title: string
    items: { url: string; step: number; caption: string | null }[]
    idx: number
  },
  // in-app confirm dialog (replaces window.confirm); the resolver is kept out of
  // reactive state — see askConfirm
  confirm: null as null | {
    title: string
    body?: string
    confirmLabel: string
    danger: boolean
  },
})

// the shared zoom's units differ per x-axis mode (step vs ms vs elapsed seconds),
// so switching mode invalidates any active zoom
watch(
  () => state.xAxis,
  () => {
    state.xRange = null
  },
)

export const visibleRuns = computed(() => {
  const q = state.search.trim().toLowerCase()
  if (!q) return state.runs
  return state.runs.filter(
    (r) =>
      r.name.toLowerCase().includes(q) ||
      r.id.includes(q) ||
      r.project.toLowerCase().includes(q) ||
      (r.group?.toLowerCase().includes(q) ?? false) ||
      r.tags.some((t) => t.toLowerCase().includes(q)),
  )
})

export const selectedRuns = computed(() =>
  state.runs.filter((r) => state.selected.includes(r.id)),
)

// --------------------------------------------------------------- chart model
// A chart is no longer 1:1 with a metric key. define_metric(panel=...) groups
// several keys into one multi-line chart; band=... folds a mean/_lo/_hi triple
// into one shaded series. MetricsPanel builds these descriptors; MetricChart
// renders them. See proposals A/B/C.

export interface ChartSeriesDesc {
  key: string // the mean / primary metric key
  label: string // legend label (define_metric series=, else the key)
  band?: { lo: string; hi: string } // shaded CI bounds, when declared / detected
  kind: 'line' | 'bar' | 'scatter'
}

export interface ChartDesc {
  id: string // stable across polls: `key:loss`, `panel:reward`, or `hist:<run>:<key>`
  title: string // the key, or the panel name
  panel?: string // set when this chart groups a panel of keys
  kind: 'line' | 'bar' | 'scatter' | 'histogram'
  series: ChartSeriesDesc[]
  // option A: a single-run panel colours by series; everything else colours by run
  colorBy: 'run' | 'series'
  // histograms are per-(run, key): a heatmap is inherently single-run, so the
  // descriptor pins the run it draws. Unset for line/bar/scatter charts.
  run?: api.Run
}

// a metric's display spec (run.define_metric): the first selected run that declares
// it wins — specs describe the metric's meaning (win_rate is always 0..1), so runs
// that bother declaring it should agree.
export function metricSpec(key: string): api.MetricSpec | null {
  for (const r of selectedRuns.value) {
    const spec = r.metric_meta?.[key]
    if (spec) return spec
  }
  return null
}

// the leading selected run for a spec'd metric, by its declared goal direction —
// drives the "★ best" badge. Compares the latest logged value (stats[key].last).
export function bestRunFor(
  key: string,
  goal: 'max' | 'min',
): { run: api.Run; value: number } | null {
  let best: { run: api.Run; value: number } | null = null
  for (const r of selectedRuns.value) {
    const v = r.stats?.[key]?.last
    if (v === null || v === undefined) continue
    if (!best || (goal === 'max' ? v > best.value : v < best.value))
      best = { run: r, value: v }
  }
  return best
}

export const anyRunning = computed(() =>
  selectedRuns.value.some((r) => r.status === 'running'),
)

// plain click = single-select (show only this run); ctrl/cmd-click = toggle into
// the current selection for side-by-side comparison
export function selectRun(id: string, additive: boolean) {
  state.touched = true
  if (!additive) {
    state.selected = [id]
    return
  }
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

// marquee/bulk-action set — mutated through here so its lifecycle (set, clear,
// prune-on-refresh, filter-on-delete) stays owned in this file, like `selected`
export function markRuns(ids: string[]) {
  // skip the reactive write (and full list re-render) when the set is unchanged
  if (
    ids.length === state.marked.length &&
    ids.every((id, i) => id === state.marked[i])
  )
    return
  state.marked = ids
}

export function clearMarks() {
  if (state.marked.length) state.marked = []
}

// ---- in-app confirm dialog ------------------------------------------------
// The pending promise's resolver lives here, not in reactive state, so `state`
// stays plain data and the ConfirmDialog only needs the display fields.
let confirmResolve: ((ok: boolean) => void) | null = null

export function askConfirm(opts: {
  title: string
  body?: string
  confirmLabel?: string
  danger?: boolean
}): Promise<boolean> {
  confirmResolve?.(false) // a new prompt supersedes any dialog still open
  return new Promise((resolve) => {
    confirmResolve = resolve
    state.confirm = {
      title: opts.title,
      body: opts.body,
      confirmLabel: opts.confirmLabel ?? 'Confirm',
      danger: opts.danger ?? false,
    }
  })
}

export function resolveConfirm(ok: boolean) {
  if (!state.confirm) return
  state.confirm = null
  const resolve = confirmResolve
  confirmResolve = null
  resolve?.(ok)
}

export async function setProject(project: string) {
  state.project = project
  await refresh()
}

// batch delete (row trash button deletes one; marquee + Delete deletes many) —
// one refresh instead of one per run
export async function removeRuns(ids: string[]) {
  const doomed = new Set(ids)
  await Promise.allSettled([...doomed].map((id) => api.deleteRun(id)))
  for (const id of doomed) {
    clearEta(id)
    dropRunCaches(id)
  }
  state.runs = state.runs.filter((r) => !doomed.has(r.id))
  state.selected = state.selected.filter((s) => !doomed.has(s))
  state.marked = state.marked.filter((s) => !doomed.has(s))
  refresh()
}

export async function removeProject(project: string) {
  await api.deleteProject(project)
  for (const r of state.runs)
    if (r.project === project) {
      clearEta(r.id)
      dropRunCaches(r.id)
    }
  if (state.project === project) state.project = '' // refresh() falls back to the next project
  await refresh()
}

let refreshEpoch = 0

export async function refresh() {
  // Concurrent refreshes (poll tick + setProject + visibility flip) can be in
  // flight at once; tag each one and let only the newest commit, so a slow
  // response for the old project can't clobber the new project's runs.
  const myEpoch = ++refreshEpoch
  try {
    const [projects, runs] = await Promise.all([
      api.fetchProjects(),
      api.fetchRuns(state.project || undefined),
    ])
    // there is no all-projects view — fall back to the most recently active project
    let nextProject = state.project
    let nextRuns = runs
    if (!projects.some((p) => p.project === state.project)) {
      nextProject = projects[0]?.project ?? ''
      nextRuns = nextProject ? await api.fetchRuns(nextProject) : runs
    }
    if (myEpoch !== refreshEpoch) return // superseded by a newer refresh — drop stale data
    state.projects = projects
    state.project = nextProject
    state.runs = nextRuns
    state.offline = false
    // drop marquee marks for runs that vanished (deleted, project switch)
    if (state.marked.length)
      state.marked = state.marked.filter((id) =>
        nextRuns.some((r) => r.id === id),
      )
    if (!state.ready) {
      // honour the restored (localStorage / URL) selection, dropping ids that no
      // longer exist; fall back to the most recent runs so the page isn't empty
      const valid = state.selected.filter((id) =>
        state.runs.some((r) => r.id === id),
      )
      state.selected = valid.length
        ? valid
        : state.runs.slice(0, 4).map((r) => r.id)
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
  if (document.hidden) return // backgrounded tab: stop polling; visibilitychange resumes it
  clearTimeout(timer)
  const delay = state.runs.some((r) => r.status === 'running')
    ? pollMs
    : IDLE_POLL_MS
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
      if (me.protected) {
        // single-key server: probe a read — the stored cookie may already unlock it
        try {
          await api.fetchProjects()
        } catch (err) {
          if (err instanceof api.HttpError && err.status === 401) {
            state.auth.mode = 'key'
            return // key gate shows; submitKey() re-enters bootstrap
          }
        }
      }
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

export async function submitKey(key: string): Promise<boolean> {
  // cookie (not header) so <img> media requests authenticate too
  document.cookie = `pandm_key=${encodeURIComponent(key)}; path=/; max-age=31536000; SameSite=Lax`
  try {
    await api.fetchProjects()
  } catch {
    return false // wrong key: stay on the gate
  }
  state.auth.mode = 'loading'
  await bootstrap()
  return true
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
const PREF_FIELDS = [
  'tab',
  'columns',
  'smoothing',
  'xAxis',
  'logScale',
  'selected',
  'live',
  'sidebarWidth',
] as const

try {
  const saved = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')
  for (const f of PREF_FIELDS) {
    // match both primitive type and array-ness so a corrupted `selected` can't
    // become a non-array and break `.includes`
    if (
      f in saved &&
      typeof saved[f] === typeof state[f] &&
      Array.isArray(saved[f]) === Array.isArray(state[f])
    )
      (state as any)[f] = saved[f]
  }
} catch {
  /* corrupted prefs are simply ignored */
}

watchEffect(() => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify(Object.fromEntries(PREF_FIELDS.map((f) => [f, state[f]]))),
  )
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
  if (!Number.isNaN(smooth))
    state.smoothing = Math.min(0.99, Math.max(0, smooth))
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
  if (state.touched && state.selected.length)
    params.set('runs', state.selected.join(','))
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

function cached<T>(
  map: Map<string, CacheEntry<T>>,
  run: api.Run,
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
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

// series/media/histogram entries are keyed by `${runId} ${key}` or plain runId;
// deleting a run must release its (up to 6000-point) cached series, or a
// long-lived tab only ever grows
export function dropRunCaches(runId: string) {
  for (const map of [seriesCache, mediaCache, histogramCache] as Map<
    string,
    unknown
  >[]) {
    for (const k of map.keys())
      if (k === runId || k.startsWith(`${runId} `)) map.delete(k)
  }
  hkVersion.delete(runId)
  delete histogramKeysByRun[runId]
}

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
    if (
      prev &&
      prev.steps.length > 0 &&
      prev.steps.length < MAX_CLIENT_POINTS
    ) {
      const lastStep = prev.steps[prev.steps.length - 1]
      const tail = await api.fetchSeries(run.id, key, lastStep)
      // keep the append idempotent: drop any tail point at/below the last step we
      // already hold, so a boundary/non-monotonic overlap can't duplicate points
      let start = 0
      while (start < tail.steps.length && tail.steps[start] <= lastStep) start++
      return {
        steps: [...prev.steps, ...tail.steps.slice(start)],
        values: [...prev.values, ...tail.values.slice(start)],
        ts: [...prev.ts, ...tail.ts.slice(start)],
      }
    }
    return api.fetchSeries(run.id, key)
  })()
  promise.catch(() => seriesCache.delete(ck)) // don't cache failures
  seriesCache.set(ck, { updated: run.updated_at, promise })
  return promise
}

export const getMedia = (run: api.Run) =>
  cached(mediaCache, run, run.id, () => api.fetchMedia(run.id))

// histograms aren't in run.stats (those are metric-only), so a run's distribution
// keys are discovered with a dedicated request, cached by updated_at. The reactive
// map drives the dashboard's Distributions section; getHistogram fetches a series.
const histogramCache = new Map<string, CacheEntry<api.HistogramSeries>>()

export function getHistogram(
  run: api.Run,
  key: string,
): Promise<api.HistogramSeries> {
  return cached(histogramCache, run, `${run.id} ${key}`, () =>
    api.fetchHistogramSeries(run.id, key),
  )
}

export const histogramKeysByRun = reactive<Record<string, string[]>>({})
const hkVersion = new Map<string, number>()

export async function ensureHistogramKeys(run: api.Run): Promise<void> {
  if (hkVersion.get(run.id) === run.updated_at) return // already current for this revision
  hkVersion.set(run.id, run.updated_at)
  try {
    const keys = await api.fetchHistogramKeys(run.id)
    histogramKeysByRun[run.id] = keys.map((k) => k.key)
  } catch {
    /* a run with no histograms / a transient error simply contributes nothing */
  }
}
