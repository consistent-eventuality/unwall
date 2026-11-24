import { useCallback, useEffect, useState } from 'react'
import { api, type Job } from './api'
import Home from './Home'
import Processing from './Processing'
import Published from './Published'
import Studio from './Studio'

const jobFromPath = () => window.location.pathname.match(/^\/studio\/([a-f0-9]{12})$/)?.[1] ?? null

export function Logo({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2 text-[17px] font-semibold tracking-tight" aria-label="Unwall home">
      <span className="grid h-6 w-6 grid-cols-2 gap-[3px]" aria-hidden>
        <i className="rounded-[2px] bg-signal" />
        <i className="rounded-[2px] bg-paper/25" />
        <i className="rounded-[2px] bg-paper/25" />
        <i className="rounded-[2px] bg-signal" />
      </span>
      Unwall
    </button>
  )
}

export default function App() {
  const [jobId, setJobId] = useState(jobFromPath)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  const go = useCallback((id: string | null) => {
    window.history.pushState(null, '', id ? `/studio/${id}` : '/')
    setJob(null)
    setError(null)
    setJobId(id)
  }, [])

  useEffect(() => {
    const onPop = () => {
      setJob(null)
      setError(null)
      setJobId(jobFromPath())
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const status = job?.status
  const settled = status === 'review' || status === 'published' || status === 'failed'

  useEffect(() => {
    if (!jobId || settled) return
    let live = true
    let timer: number
    const poll = async () => {
      try {
        const next = await api.job(jobId)
        if (live) setJob(next)
        if (live && !['review', 'published', 'failed'].includes(next.status)) timer = window.setTimeout(poll, 900)
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : 'Could not load this video.')
      }
    }
    poll()
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [jobId, settled])

  if (!jobId) return <Home onStart={go} />
  if (error) {
    return (
      <main className="grid min-h-screen place-items-center px-6 text-center">
        <div>
          <h1 className="font-serif text-4xl">We couldn't open that video</h1>
          <p className="mt-3 text-soft">{error}</p>
          <button onClick={() => go(null)} className="mt-6 rounded-full bg-signal px-5 py-2.5 font-semibold text-ink">Back to start</button>
        </div>
      </main>
    )
  }
  if (!job || !['review', 'published'].includes(job.status)) return <Processing job={job} jobId={jobId} onHome={() => go(null)} />
  if (job.status === 'published') return <Published job={job} onEdit={setJob} onChange={setJob} onHome={() => go(null)} />
  return <Studio key={job.id} job={job} onChange={setJob} onHome={() => go(null)} />
}
