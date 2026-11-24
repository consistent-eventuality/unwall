export type ClaimSource = 'speech' | 'on_screen' | 'visual'

export interface Claim {
  text: string
  start_s: number
  end_s: number
  source: ClaimSource
  evidence: string
}

export interface Draft {
  title: string
  question: string
  answer: string
  claims: Claim[]
  related_questions: string[]
  topics: string[]
  watch_reason: string
  needs_review: string[]
}

export interface Meta {
  creator: string
  credential: string
  platform: string
  followers: string
  profile_url: string
  source_url: string
  views: string
  views_count: string
  stats_date: string
  posted_date: string
}

export type Status = 'queued' | 'reading' | 'listening' | 'watching' | 'writing' | 'review' | 'published' | 'failed'

export interface LaunchTarget {
  name: string
  status: 'received' | 'discoverable' | 'skipped' | 'failed'
  detail: string
}

export interface Launch {
  state: 'running' | 'live' | 'failed' | 'not_configured'
  step: string | null
  error: string | null
  url: string | null
  targets: LaunchTarget[]
}

export interface Job {
  launch?: Launch | null
  id: string
  status: Status
  error: string | null
  filename: string
  sample: string | null
  duration: number
  transcript: { start: number; end: number; text: string }[]
  frames: { t: number; file: string }[]
  draft: Draft | null
  slug: string | null
  url: string | null
  approved_at: string | null
  meta: Meta
}

export interface Sample {
  id: string
  title: string
  kind: string
  note: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new Error("Can't reach the local server.")
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Something went wrong. Try again.')
  }
  return response.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  samples: () => request<Sample[]>('/api/samples'),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  startSample: (id: string) => request<Job>(`/api/jobs/sample/${id}`, { method: 'POST' }),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Job>('/api/jobs', { method: 'POST', body: form })
  },
  patch: (id: string, body: { draft?: Draft; meta?: Meta }) => request<Job>(`/api/jobs/${id}`, json('PATCH', body)),
  publish: (id: string) => request<Job>(`/api/jobs/${id}/publish`, json('POST', { consent: true })),
  launch: (id: string) => request<Job>(`/api/jobs/${id}/launch`, { method: 'POST' }),
  output: async (id: string, name: 'page.md' | 'claims.json') => {
    const response = await fetch(`/api/jobs/${id}/output/${name}`)
    if (!response.ok) throw new Error('Could not load this output.')
    return response.text()
  },
}

export const stamp = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
