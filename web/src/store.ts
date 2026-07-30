import { computed, reactive, watch, watchEffect } from 'vue'
import * as api from './api'
import { runColor } from './colors'
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
  tab: 'metrics' as 'metrics' | 'media' | 'table' | 'scatter',
  columns: 0, // grid columns for metrics/media, 0 = auto
  mediaSteps: [] as number[], // union of logged media steps, kept fresh by MediaPanel
  mediaIdx: 0, // index into mediaSteps, driven by the toolbar step slider
  smoothing: 0,
  xAxis: 'step' as 'step' | 'time' | 'rtime', // rtime = elapsed since each run's start
  xRange: null as [number, number] | null, // shared x zoom (units follow xAxis); null = full range
  logScale: false,
  expandedChart: null as string | null,
  // 107 keys in one run is normal — a substring filter over key / panel / section
  metricSearch: '',
  // one training split across resumed runs (bc-env, bc-env-r1, …) is one curve:
  // stitch same-group runs into a single continuous line instead of N lines
  stitchGroups: false,
  // comparing runs normally dissolves panels into per-key charts; keep them whole
  // instead and draw one copy of the panel per run, side by side
  keepPanels: false,
  showDebug: false, // the importance="debug" fold at the page bottom
  // run-level scatter (one point per run): which metric on each axis, and which
  // per-run aggregate of it to plot
  scatterX: '',
  scatterY: '',
  scatterAgg: 'last' as 'last' | 'min' | 'max',
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

// One shared 1 s clock, so the ETA countdown and the "quiet for N" line advance
// between polls (finishAt and updated_at are fixed until the next fetch). One
// interval for the app, not one per component.
export const clock = reactive({ now: Date.now() / 1000 })
setInterval(() => (clock.now = Date.now() / 1000), 1000)

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
// into one shaded series. The `charts` computed below builds these descriptors;
// MetricsPanel lays them out and MetricChart renders them. See proposals A/B/C.

export type MetricKind =
  | 'line'
  | 'bar'
  | 'scatter'
  | 'stat'
  | 'table'
  | 'histogram'

export type Importance = 'primary' | 'normal' | 'debug'

export interface ChartSeriesDesc {
  key: string // the mean / primary metric key
  label: string // legend label (define_metric series=, else the key)
  band?: { lo: string; hi: string } // shaded CI bounds, when declared / detected
  kind: 'line' | 'bar' | 'scatter' | 'stat' | 'table'
  axis?: 'left' | 'right' // opt-in second y-scale inside a panel (define_metric axis=)
  row?: string // row label inside a kind="table" panel
  description?: string // this member's own note — panels surface them in the tooltip
}

export interface ChartDesc {
  id: string // stable across polls: `key:loss`, `panel:reward`, or `hist:<run>:<key>`
  title: string // the key, or the panel name
  panel?: string // set when this chart groups a panel of keys
  kind: MetricKind
  series: ChartSeriesDesc[]
  // option A: a single-run panel colours by series; everything else colours by run
  colorBy: 'run' | 'series'
  // histograms are per-(run, key): a heatmap is inherently single-run, so the
  // descriptor pins the run it draws. Unset for line/bar/scatter charts.
  run?: api.Run
  // keepPanels: a panel drawn once per lane, so two runs show the same panel side
  // by side instead of dissolving it into per-key charts
  lane?: Lane
  importance: Importance // page position: pinned row / normal grid / debug fold
  alarm?: api.AlarmSpec // declared threshold — renders as a badge, not a chart
  // members whose specs disagree on an axis-defining field. A panel takes its axis
  // from its members, so a silent disagreement used to flatten the smaller line
  // against the axis with no hint why.
  conflicts?: string[]
}

const IMPORTANCE_RANK: Record<Importance, number> = {
  primary: 0,
  normal: 1,
  debug: 2,
}

/** A panel is as important as its most important member — declaring one member
 *  `primary` promotes the chart it lands in. */
export function mergeImportance(values: Importance[]): Importance {
  return values.reduce(
    (acc, v) => (IMPORTANCE_RANK[v] < IMPORTANCE_RANK[acc] ? v : acc),
    'debug' as Importance,
  )
}

export const importanceRank = (i: Importance) => IMPORTANCE_RANK[i]

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

// Fields that shape an axis rather than a single line. A panel used to take all of
// them from whichever member sorted first, so two members declaring different units
// (or one declaring bounds the other contradicts) silently lost one of them — and a
// member left flat against the axis gave no hint why. Now the panel merges its
// members, first declaration wins, and every disagreement is reported.
const AXIS_FIELDS = [
  'unit',
  'min',
  'max',
  'baseline',
  'scale',
  'goal',
  'x_label',
  'y_label',
  'x_ticks',
  'y_ticks',
] as const

/** Merge the axis-shaping half of several keys' specs into the one spec their shared
 *  axis will use, plus the names of the fields the members disagree on. */
export function resolveAxisSpec(keys: string[]): {
  spec: api.MetricSpec
  conflicts: string[]
} {
  const spec: Record<string, unknown> = {}
  const conflicts: string[] = []
  for (const key of keys) {
    const s = metricSpec(key) as Record<string, unknown> | null
    if (!s) continue
    for (const f of AXIS_FIELDS) {
      const v = s[f]
      if (v === undefined) continue
      if (!(f in spec)) {
        spec[f] = v
        continue
      }
      // a declared value that contradicts an earlier one: keep the first (stable
      // across polls) and name the field so the chart can flag it
      if (
        JSON.stringify(spec[f]) !== JSON.stringify(v) &&
        !conflicts.includes(f)
      )
        conflicts.push(f)
    }
  }
  return { spec: spec as api.MetricSpec, conflicts }
}

// the leading (run, key) pair by a declared goal direction — drives the "★ best"
// badge. Compares the latest logged value (stats[key].last). Several keys so a
// panel gets a badge too: with one member it reads "which run leads", with several
// it reads "which line leads", and both are the question the goal declares.
export function bestAcross(
  keys: string[],
  goal: 'max' | 'min',
): { run: api.Run; key: string; value: number } | null {
  let best: { run: api.Run; key: string; value: number } | null = null
  for (const r of selectedRuns.value) {
    for (const key of keys) {
      const v = r.stats?.[key]?.last ?? r.summary?.[key]
      if (v === null || v === undefined) continue
      if (!best || (goal === 'max' ? v > best.value : v < best.value))
        best = { run: r, key, value: v }
    }
  }
  return best
}

// ------------------------------------------------------------------ lanes
// A chart draws one line per *lane*, not per run. Normally a lane is a run. With
// stitchGroups on, every run sharing a `group` collapses into one lane whose
// segments are read back to back: one training split across resumed runs
// (bc-env, bc-env-r1, …) has a continuous step axis, so it is one curve, and
// reading a whole run stops meaning multi-selecting five sidebar rows.

export interface Lane {
  id: string // run id, or `group:<name>`
  label: string // run name, or the group name
  color: string
  runs: api.Run[] // one run, or the group's runs in launch order
  group?: string
}

export const lanes = computed<Lane[]>(() => {
  const runs = selectedRuns.value
  if (!state.stitchGroups)
    return runs.map((r) => ({
      id: r.id,
      label: r.name,
      color: runColor(r.id),
      runs: [r],
    }))
  const out: Lane[] = []
  const byGroup = new Map<string, api.Run[]>()
  for (const r of runs) {
    if (!r.group) {
      out.push({ id: r.id, label: r.name, color: runColor(r.id), runs: [r] })
      continue
    }
    if (!byGroup.has(r.group)) byGroup.set(r.group, [])
    byGroup.get(r.group)!.push(r)
  }
  for (const [group, members] of byGroup) {
    const ordered = [...members].sort((a, b) => a.created_at - b.created_at)
    out.push({
      id: `group:${group}`,
      label: group,
      // the group wears its first segment's colour, so stitching a selection
      // doesn't repaint the run you were already looking at
      color: runColor(ordered[0].id),
      runs: ordered,
      group,
    })
  }
  return out
})

// stitching only means something when the selection actually spans a group, and
// keeping panels whole only when there's more than one run to keep them for — the
// toolbar hides both otherwise rather than offering dead switches
export const groupedSelection = computed(() => {
  const groups = new Set(
    selectedRuns.value.map((r) => r.group).filter(Boolean) as string[],
  )
  for (const g of groups)
    if (selectedRuns.value.filter((r) => r.group === g).length > 1) return true
  return false
})

// ------------------------------------------------------------------ alarms
// define_metric(alarm=…) metrics are the ones whose only value is the moment they
// break: a truncation rate that must stay 0, an OOM counter. As a chart they are a
// permanently flat line taking a grid slot; as a badge they cost nothing until they
// trip. Evaluated over the run's whole history — stats carries min/max, so a
// violation two hours ago is still caught, which is the entire point for a run
// nobody watches.

export interface AlarmState {
  key: string
  label: string
  spec: api.AlarmSpec
  chartId: string // the chart to open when a tripped badge is clicked
  violated: boolean
  value: number | null // the offending extreme when violated, else the latest value
  run: api.Run | null // which run tripped it
  unit?: string
}

/** The offending extreme value, or null when the metric's whole history holds. */
function alarmBreach(spec: api.AlarmSpec, s: api.MetricStats): number | null {
  if (spec.ok !== undefined) {
    if (s.max > spec.ok) return s.max
    if (s.min < spec.ok) return s.min
  }
  if (spec.max !== undefined && s.max > spec.max) return s.max
  if (spec.min !== undefined && s.min < spec.min) return s.min
  return null
}

/** One badge per alarm-declared key, violations first. */
export const alarms = computed<AlarmState[]>(() => {
  const out: AlarmState[] = []
  const seen = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const [key, spec] of Object.entries(run.metric_meta ?? {})) {
      if (!spec?.alarm || seen.has(key)) continue
      seen.add(key)
      let breach: { run: api.Run; value: number } | null = null
      let latest: { run: api.Run; value: number } | null = null
      for (const r of selectedRuns.value) {
        const s = r.stats?.[key]
        if (!s) continue
        if (s.last !== null && !latest) latest = { run: r, value: s.last }
        const v = alarmBreach(spec.alarm, s)
        if (v !== null && !breach) breach = { run: r, value: v }
      }
      out.push({
        key,
        label: spec.series ?? key,
        spec: spec.alarm,
        chartId: `key:${key}`,
        violated: !!breach,
        value: (breach ?? latest)?.value ?? null,
        run: (breach ?? latest)?.run ?? null,
        unit: spec.unit,
      })
    }
  }
  return out.sort(
    (a, b) =>
      Number(b.violated) - Number(a.violated) || a.key.localeCompare(b.key),
  )
})

/** Human form of a threshold, for the badge tooltip: `must be 0`, `≤ 0.01`. */
export function alarmBound(spec: api.AlarmSpec): string {
  const parts: string[] = []
  if (spec.ok !== undefined) parts.push(`= ${spec.ok}`)
  if (spec.max !== undefined) parts.push(`≤ ${spec.max}`)
  if (spec.min !== undefined) parts.push(`≥ ${spec.min}`)
  return parts.join(' and ')
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
  'stitchGroups',
  'keepPanels',
  'showDebug',
  'scatterX',
  'scatterY',
  'scatterAgg',
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
  if (
    tab === 'metrics' ||
    tab === 'media' ||
    tab === 'table' ||
    tab === 'scatter'
  )
    state.tab = tab
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
  const q = params.get('q')
  if (q) state.metricSearch = q
  if (params.get('stitch') === '1') state.stitchGroups = true
  if (params.get('panels') === '1') state.keepPanels = true
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
  if (state.metricSearch) params.set('q', state.metricSearch)
  if (state.stitchGroups) params.set('stitch', '1')
  if (state.keepPanels) params.set('panels', '1')
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
  delete mediaByRun[runId]
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

// ------------------------------------------------------------ chart list
// The chart descriptors the Metrics tab renders. They live here, not in
// MetricsPanel, because the tab bar counts them too — a count that only exists
// while its panel is mounted cannot label a tab you have not opened yet.

// the runs polling already carries per-key stats — their keys are exactly the
// metric keys, so chart discovery needs no per-run /metrics requests
const unionKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const k of Object.keys(run.stats)) set.add(k)
    // bar / stat / table metrics may carry no time series (their value is the latest
    // or summary scalar), so surface keys declared as one even when stats has nothing
    for (const [k, spec] of Object.entries(run.metric_meta ?? {})) {
      if (
        spec?.kind === 'bar' ||
        spec?.kind === 'stat' ||
        spec?.kind === 'table'
      )
        set.add(k)
    }
  }
  return [...set].sort()
})

// a panel holding mixed kinds is an authoring mistake; resolve it deterministically
// (most structural wins) rather than by whichever key sorted first
const KIND_PRIORITY: MetricKind[] = ['table', 'bar', 'stat', 'scatter', 'line']

/** One chart's worth of keys, before it knows how many lanes it must serve. */
interface Group {
  id: string
  title: string
  panel?: string
  members: string[]
}

// Build the chart descriptors from the union of keys + their declared specs:
// fold band triples, drop the consumed _lo/_hi keys, group panels, then expand each
// group over the lanes it needs.
export const charts = computed<ChartDesc[]>(() => {
  const keys = unionKeys.value
  const present = new Set(keys)
  const laneList = lanes.value
  const multiLane = laneList.length > 1
  // resolve each key's spec once — metricSpec() scans the selected runs, and the
  // steps below would otherwise look the same key up several times per rebuild
  const specOf = new Map(keys.map((k) => [k, metricSpec(k)]))

  // 1. band detection — map each mean key to its lo/hi bounds, and remember which
  // keys are bounds so they don't also render as their own charts. Always explicit:
  // band=true pairs the _lo/_hi siblings, band={lo,hi} names them. (No silent
  // suffix magic — bare _lo/_hi keys stay ordinary charts unless a band is declared.)
  const consumed = new Set<string>()
  const bandOf = new Map<string, { lo: string; hi: string }>()
  for (const k of keys) {
    const band = specOf.get(k)?.band
    let lo: string | undefined
    let hi: string | undefined
    if (band && typeof band === 'object') {
      lo = band.lo
      hi = band.hi
    } else if (band === true) {
      lo = `${k}_lo`
      hi = `${k}_hi`
    }
    if (lo && hi && present.has(lo) && present.has(hi)) {
      bandOf.set(k, { lo, hi })
      consumed.add(lo)
      consumed.add(hi)
    }
  }

  const seriesFor = (k: string): ChartSeriesDesc => {
    const spec = specOf.get(k)
    return {
      key: k,
      label: spec?.series ?? k,
      band: bandOf.get(k),
      kind: spec?.kind ?? 'line',
      axis: spec?.axis,
      row: spec?.row,
      description: spec?.description,
    }
  }
  const importanceOf = (k: string): Importance =>
    specOf.get(k)?.importance ?? 'normal'

  // Declaration order, for everything a panel puts in a sequence: legend entries,
  // bar categories, table rows and columns. Key order would be the easy answer, but
  // then controlling the order would mean renaming keys — and a key is data, not a
  // display knob: renaming one tears the metric's history in two and breaks
  // cross-run comparison. metric_meta preserves the order define_metric was called
  // in (both backends merge specs by appending), so the author's order is already
  // recorded; this just reads it. Unseen keys sort last, alphabetically.
  const declOrder = new Map<string, number>()
  for (const r of selectedRuns.value)
    for (const k of Object.keys(r.metric_meta ?? {}))
      if (!declOrder.has(k)) declOrder.set(k, declOrder.size)
  const byDeclaration = (a: string, b: string) =>
    (declOrder.get(a) ?? Infinity) - (declOrder.get(b) ?? Infinity) ||
    a.localeCompare(b)

  // 2/3. collect groups. A panel collapses its keys into one chart; a lone key is a
  // group of one. Option A: panels only collapse in single-lane view — with several
  // runs each key falls back to its own chart (coloured by run) so run-comparison
  // still works. Exceptions: bar (a one-category bar is meaningless), table (a table
  // of one row is not a table), and keepPanels, which asks for the panel whole.
  const groups: Group[] = []
  const byPanel = new Map<string, Group>()
  for (const k of keys) {
    if (consumed.has(k)) continue
    const spec = specOf.get(k)
    const kind = spec?.kind ?? 'line'
    const groupable =
      !multiLane || state.keepPanels || kind === 'bar' || kind === 'table'
    // an alarm metric is a badge, not a panel line — and AlarmBar opens it by
    // the `key:` id, so it must stay its own chart
    if (spec?.panel && groupable && !spec.alarm) {
      let g = byPanel.get(spec.panel)
      if (!g) {
        g = {
          id: `panel:${spec.panel}`,
          title: spec.panel,
          panel: spec.panel,
          members: [],
        }
        byPanel.set(spec.panel, g)
        groups.push(g)
      }
      g.members.push(k)
    } else {
      groups.push({ id: `key:${k}`, title: k, members: [k] })
    }
  }

  const out: ChartDesc[] = []
  for (const g of groups) {
    const kinds: MetricKind[] = g.members.map(
      (k) => specOf.get(k)?.kind ?? 'line',
    )
    const kind = KIND_PRIORITY.find((p) => kinds.includes(p)) ?? 'line'
    const series = [...g.members].sort(byDeclaration).map(seriesFor)
    const importance = mergeImportance(g.members.map(importanceOf))
    const alarm = specOf.get(g.members[0])?.alarm
    // a panel takes its axis from its members: report the fields they disagree on,
    // per side, so a right-axis member isn't blamed for differing from the left one
    const conflicts = (['left', 'right'] as const).flatMap(
      (side) =>
        resolveAxisSpec(
          series.filter((s) => (s.axis ?? 'left') === side).map((s) => s.key),
        ).conflicts,
    )
    // a table cell holds one number, so a table is per-run; a kept panel is drawn
    // once per lane so two runs show the same panel side by side
    const perLane =
      multiLane && (kind === 'table' || (!!g.panel && state.keepPanels))
    const base = {
      title: g.title,
      panel: g.panel,
      kind,
      series,
      importance,
      alarm,
      conflicts: conflicts.length ? conflicts : undefined,
    }
    if (perLane)
      for (const lane of laneList)
        out.push({ ...base, id: `${g.id}@${lane.id}`, colorBy: 'series', lane })
    else
      out.push({
        ...base,
        id: g.id,
        colorBy: multiLane || !g.panel ? 'run' : 'series',
      })
  }

  // 4. histograms (run.log_histogram) join the same chart model so they get the
  // identical title / description / expand treatment — but a heatmap is single-run,
  // so each (run, key) is its own descriptor pinned to that run. They sort into the
  // same prefix sections as their sibling metrics (dist/* lands under "dist").
  for (const run of selectedRuns.value) {
    for (const key of histogramKeysByRun[run.id] ?? []) {
      out.push({
        id: `hist:${run.id}:${key}`,
        title: key,
        kind: 'histogram',
        series: [{ key, label: key, kind: 'line' }],
        colorBy: 'run',
        run,
        importance: importanceOf(key),
      })
    }
  }
  return out
})

// 107 keys with no way to search them was its own problem. Space-separated terms
// all have to match (narrowing, not widening), against the title, the panel name,
// and every member key / legend label.
const chartMatches = computed(() => {
  const terms = state.metricSearch
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
  if (!terms.length) return () => true
  return (c: ChartDesc) => {
    const hay = [
      c.title,
      c.panel ?? '',
      ...c.series.flatMap((s) => [s.key, s.label]),
    ]
      .join(' ')
      .toLowerCase()
    return terms.every((t) => hay.includes(t))
  }
})

export const visibleCharts = computed(() =>
  charts.value.filter(chartMatches.value),
)

// alarm metrics live in the badge bar, not the grid
export const gridCharts = computed(() =>
  visibleCharts.value.filter((c) => !c.alarm),
)

// ---------------------------------------------------------------- media
// MediaPanel renders these; the tab bar counts them, so the fetch can't wait for
// the panel to mount.
export const mediaByRun = reactive<Record<string, api.MediaItem[]>>({})

watchEffect(() => {
  for (const run of selectedRuns.value) {
    // One fetch per run is enough for the count. Only once the Media tab is open
    // does this follow updated_at, so a running run's new images keep arriving —
    // re-reading every selection's media on every poll regardless of the visible
    // tab would bill reads nobody is looking at.
    if (state.tab !== 'media' && mediaByRun[run.id] !== undefined) continue
    getMedia(run)
      .then((items) => {
        mediaByRun[run.id] = items
      })
      .catch(() => {})
  }
})

// every image the selection holds, not the subset the step slider happens to show:
// a tab count that changed while you dragged the slider would be noise
export const mediaCount = computed(() =>
  selectedRuns.value.reduce((n, r) => n + (mediaByRun[r.id]?.length ?? 0), 0),
)

// -------------------------------------------------------- run-level scatter
// One point per run: values come from run.stats — the per-key aggregates the run
// list already carries — so the whole scatter costs zero extra requests no matter
// how many runs are in it. Here rather than in ScatterPanel for the same reason as
// the charts above: the tab bar needs the point count before the panel mounts.

// every metric any listed run logged — the scatter spans the sidebar's filtered
// list, not the compare selection, so a 40-run sweep plots without selecting 40 rows
export const scatterKeys = computed(() => {
  const set = new Set<string>()
  for (const r of visibleRuns.value)
    for (const k of Object.keys(r.stats)) set.add(k)
  return [...set].sort()
})

// first load / a project with different metrics: fall back to the first two keys
watch(
  scatterKeys,
  (list) => {
    if (!list.length) return
    if (!list.includes(state.scatterX)) state.scatterX = list[0]
    if (!list.includes(state.scatterY)) state.scatterY = list[1] ?? list[0]
  },
  { immediate: true },
)

export interface ScatterPoint {
  run: api.Run
  x: number
  y: number
}

export const scatterPoints = computed<ScatterPoint[]>(() => {
  const agg = state.scatterAgg
  const out: ScatterPoint[] = []
  for (const run of visibleRuns.value) {
    const x = run.stats?.[state.scatterX]?.[agg]
    const y = run.stats?.[state.scatterY]?.[agg]
    if (x === null || x === undefined || y === null || y === undefined) continue
    out.push({ run, x, y })
  }
  return out
})

// ------------------------------------------------------------ tab counts
// What each tab holds for the current selection, so the tab bar can say so before
// you click into it. Null means "nothing to count" and renders no bubble at all —
// an empty tab is explained by its own empty state, not by a grey zero.
export const tabCounts = computed<Record<typeof state.tab, number | null>>(
  () => ({
    metrics: gridCharts.value.length || null,
    media: mediaCount.value || null,
    table: selectedRuns.value.length || null, // one row per run
    scatter: scatterPoints.value.length || null,
  }),
)
