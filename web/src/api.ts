export interface Project {
  project: string
  runs: number
  last_active: number
}

export interface Run {
  id: string
  project: string
  name: string
  status: 'running' | 'finished' | 'crashed'
  config: Record<string, unknown>
  created_at: number
  updated_at: number
  finished_at: number | null
  summary: Record<string, number>
}

export interface MetricKey {
  key: string
  points: number
  last_step: number
}

export interface Series {
  steps: number[]
  values: number[]
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

async function get<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`${resp.status} ${url}`)
  return resp.json()
}

export const fetchProjects = () => get<Project[]>('/api/projects')

export const fetchRuns = (project?: string) =>
  get<Run[]>(`/api/runs${project ? `?project=${encodeURIComponent(project)}` : ''}`)

export const fetchMetricKeys = (runId: string) => get<MetricKey[]>(`/api/runs/${runId}/metrics`)

export const fetchSeries = (runId: string, key: string) =>
  get<Series>(`/api/runs/${runId}/metrics/${encodeURIComponent(key)}`)

export const fetchMedia = (runId: string) => get<MediaItem[]>(`/api/runs/${runId}/media`)
