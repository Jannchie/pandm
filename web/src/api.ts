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

export interface Me {
  mode: 'local' | 'user'
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
  get<Run[]>(`/api/runs${project ? `?project=${encodeURIComponent(project)}` : ''}`)

export const fetchSeries = (runId: string, key: string) =>
  get<Series>(`/api/runs/${runId}/metrics/${encodeURIComponent(key)}`)

export const fetchMedia = (runId: string) => get<MediaItem[]>(`/api/runs/${runId}/media`)

export async function deleteRun(runId: string): Promise<void> {
  const resp = await fetch(`/api/runs/${runId}`, { method: 'DELETE' })
  if (!resp.ok) throw new HttpError(resp.status, `/api/runs/${runId}`)
}
