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
  progress: number | null // current step/epoch/sample, for ETA
  progress_total: number | null // target; null = unknown, no ETA
  progress_ts: number | null // when progress was last reported
  summary: Record<string, number> // author-written run-level scalars (run.summary({...}))
  stats: Record<string, MetricStats> // per-key aggregates; .last is the latest logged value
  metric_meta: Record<string, MetricSpec> // author-declared per-metric display specs (run.define_metric)
}

export interface MetricStats {
  min: number
  max: number
  count: number
  last: number | null
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
  kind?: 'line' | 'bar' | 'scatter' // chart type (default 'line')
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
