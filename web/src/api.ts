export interface Project {
  project: string
  runs: number
  last_active: number
}

export interface Run {
  id: string
  project: string
  name: string
  description: string // one-line human note (init(description=...))
  tags: string[] // free-form labels for filtering (init(tags=...))
  group: string | null // buckets related runs (init(group=...))
  status: 'running' | 'finished' | 'crashed'
  config: Record<string, unknown>
  created_at: number
  updated_at: number
  finished_at: number | null
  // resume-aware training time: sum of prior launch segments, plus the current
  // segment measured from segment_started_at (see runDuration in fmt.ts).
  active_seconds: number
  segment_started_at: number | null // null on legacy runs -> falls back to created_at
  progress: number | null // current step/epoch/sample, for ETA
  progress_total: number | null // target; null = unknown, no ETA
  progress_ts: number | null // when progress was last reported
  summary: Record<string, number> // author-written run-level scalars (run.summary({...}))
  stats: Record<string, MetricStats> // per-key aggregates; .last is the latest logged value
  metric_meta: Record<string, MetricSpec> // author-declared per-metric display specs (run.define_metric)
}

/** Wall-clock training time in seconds, resume-aware: prior launch segments
 *  (active_seconds) plus the current one, measured from segment_started_at up to
 *  finish (or, while still running/crashed, the last heartbeat). The idle gap
 *  between a finish/crash and the next resume is excluded. Legacy runs have
 *  active_seconds=0 and a null segment_started_at, collapsing to end - created_at. */
export function runDuration(run: Run): number {
  const segStart = run.segment_started_at ?? run.created_at
  const end = run.finished_at ?? run.updated_at
  return run.active_seconds + Math.max(0, end - segStart)
}

export interface MetricStats {
  min: number
  max: number
  count: number
  last: number | null
}

/** A threshold a metric must hold (run.define_metric(alarm=…)): one or more of
 *  "must equal" / ceiling / floor. Its value is the moment it breaks. */
export interface AlarmSpec {
  ok?: number
  max?: number
  min?: number
}

/** A run whose process went quiet without anyone writing down how it ended: it was
 *  OOM-killed, pod-evicted or `kill -9`'d, so no exit handler ran. The store already
 *  presumes such a run crashed once its 15 s heartbeat has been silent for a minute —
 *  but that verdict is an *inference*, and reporting it as plain `crashed` claims more
 *  than is known: nothing distinguished "the trainer raised and said so" from "the
 *  process vanished and we guessed".
 *
 *  The two are exactly separable, by the same rule `pandm finish --stale` uses: a real
 *  crash (excepthook, `run.finish("crashed")`, `pandm finish`) writes `finished_at`;
 *  an inferred one never does. So this needs no clock, no threshold of its own, and no
 *  protocol change — and it self-heals if the process turns out to be alive and
 *  heartbeats again. */
export function runStale(run: Run): boolean {
  return run.status === 'crashed' && run.finished_at === null
}

/** running / stale / finished / crashed — the state to *show*, not the stored one. */
export function runState(
  run: Run,
): 'running' | 'stale' | 'finished' | 'crashed' {
  return runStale(run) ? 'stale' : run.status
}

/** How the dashboard should render a metric — declared via run.define_metric. */
export interface MetricSpec {
  min?: number // fixed y-axis lower bound
  max?: number // fixed y-axis upper bound
  unit?: string // 'percent' -> show 0.73 as 73%, default range 0..1
  goal?: 'max' | 'min' // which direction is "better" (marks the leading run)
  baseline?: number // draws a dashed reference line (e.g. chance level)
  description?: string // one-line human note shown under the chart
  panel?: string // keys sharing a panel render as lines in one chart
  series?: string // legend label for this key's line (defaults to the key)
  band?: boolean | { lo: string; hi: string } // shaded CI: true = _lo/_hi suffix, or explicit keys
  kind?: 'line' | 'bar' | 'scatter' | 'stat' | 'table' // chart type (default 'line')
  importance?: 'primary' | 'normal' | 'debug' // page hierarchy: pinned / normal / folded away
  alarm?: AlarmSpec // threshold that must hold; collapses the metric to a badge
  axis?: 'left' | 'right' // panel member's y-axis (opt-in second scale)
  scale?: 'linear' | 'log' // this metric's y-axis scale (default linear)
  row?: string // row label inside a kind="table" panel (the entity this key describes)
  x_label?: string // x-axis title (e.g. 'Episode'); applies to every chart type
  y_label?: string // y-axis title (e.g. 'Reward')
  x_ticks?: string[] // categorical x tick labels, positional — only on a category axis (bar)
  y_ticks?: string[] // categorical y tick labels, positional — only on a category axis (histogram bins)
}

export interface Series {
  steps: number[]
  values: number[]
  ts: number[]
}

export interface HistogramKey {
  key: string
  points: number
  last_step: number
}

/** A series of binned distributions over time — bins[i]/counts[i] is the snapshot
 *  at steps[i]. bins[i] holds n+1 edges, counts[i] the n per-bin counts. */
export interface HistogramSeries {
  steps: number[]
  bins: number[][]
  counts: number[][]
  ts: number[]
}

export interface MediaItem {
  key: string
  step: number
  filename: string
  caption: string | null
  ts: number
  url: string
}

export interface Me {
  mode: 'local' | 'user'
  protected?: boolean // single-key server: reads need the key (cookie or header)
  login?: string
  name?: string | null
  avatar_url?: string | null
  api_key?: string
}

export class HttpError extends Error {
  constructor(
    public status: number,
    url: string,
  ) {
    super(`${status} ${url}`)
  }
}

async function get<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new HttpError(resp.status, url)
  return resp.json()
}

export const fetchMe = () => get<Me>('/api/me')

export const logout = () => fetch('/api/auth/logout', { method: 'POST' })

export async function rotateApiKey(): Promise<string> {
  const resp = await fetch('/api/me/key/rotate', { method: 'POST' })
  if (!resp.ok) throw new HttpError(resp.status, '/api/me/key/rotate')
  return (await resp.json()).api_key
}

export async function approveCli(code: string): Promise<boolean> {
  const resp = await fetch('/api/cli/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
  return resp.ok
}

export const fetchProjects = () => get<Project[]>('/api/projects')

export const fetchRuns = (project?: string) =>
  get<Run[]>(
    `/api/runs${project ? `?project=${encodeURIComponent(project)}` : ''}`,
  )

export const fetchSeries = (runId: string, key: string, afterStep?: number) =>
  get<Series>(
    `/api/runs/${runId}/metrics/${encodeURIComponent(key)}${afterStep !== undefined ? `?after_step=${afterStep}` : ''}`,
  )

export const fetchMedia = (runId: string) =>
  get<MediaItem[]>(`/api/runs/${runId}/media`)

export const fetchHistogramKeys = (runId: string) =>
  get<HistogramKey[]>(`/api/runs/${runId}/histograms`)

export const fetchHistogramSeries = (runId: string, key: string) =>
  get<HistogramSeries>(
    `/api/runs/${runId}/histograms/${encodeURIComponent(key)}`,
  )

export async function deleteRun(runId: string): Promise<void> {
  const resp = await fetch(`/api/runs/${runId}`, { method: 'DELETE' })
  if (!resp.ok) throw new HttpError(resp.status, `/api/runs/${runId}`)
}

export async function deleteProject(project: string): Promise<void> {
  const url = `/api/projects/${encodeURIComponent(project)}`
  const resp = await fetch(url, { method: 'DELETE' })
  if (!resp.ok) throw new HttpError(resp.status, url)
}
