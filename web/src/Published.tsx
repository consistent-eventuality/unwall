import { useEffect, useState } from 'react'
import { api, type Job, type LaunchTarget } from './api'
import { Logo } from './App'

type View = 'page' | 'page.md' | 'claims.json'
const VIEWS: { key: View; label: string; note: string }[] = [
  { key: 'page', label: 'People see', note: 'A readable page that sends visitors to the original.' },
  { key: 'page.md', label: 'Agents read', note: 'Clean text with attribution and timestamps.' },
  { key: 'claims.json', label: 'Structured', note: 'Every claim with its evidence and source.' },
]

const STEPS = [
  ['building', 'Build the page'],
  ['deploying', 'Deploy to the public host'],
  ['verifying', 'Confirm it loads publicly'],
  ['submitting', 'Submit for indexing'],
] as const

const TARGET_STYLE: Record<LaunchTarget['status'], { label: string; className: string }> = {
  received: { label: 'Submitted', className: 'text-signal' },
  discoverable: { label: 'Open to crawl', className: 'text-soft' },
  skipped: { label: 'Skipped', className: 'text-faint' },
  failed: { label: 'Failed', className: 'text-bad' },
}

export default function Published({ job, onEdit, onChange, onHome }: { job: Job; onEdit: (job: Job) => void; onChange: (job: Job) => void; onHome: () => void }) {
  const [view, setView] = useState<View>('page')
  const [text, setText] = useState('')
  const [copied, setCopied] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const path = `/p/${job.slug}/`
  const launch = job.launch
  const running = launch?.state === 'running'
  const isLive = launch?.state === 'live'
  const shareUrl = isLive && launch?.url ? launch.url : job.url
  const submitFailed = isLive && launch?.targets.some((t) => t.status === 'failed')
  const stepIndex = STEPS.findIndex(([key]) => key === launch?.step)

  useEffect(() => {
    if (!running) return
    let active = true
    let timer: number
    const poll = async () => {
      try {
        const next = await api.job(job.id)
        if (!active) return
        onChange(next)
        if (next.launch?.state === 'running') timer = window.setTimeout(poll, 1200)
      } catch {
        if (active) timer = window.setTimeout(poll, 2500)
      }
    }
    timer = window.setTimeout(poll, 800)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [job.id, running, onChange])

  const retry = async () => {
    setRetryError(null)
    try {
      onChange(await api.launch(job.id))
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : 'Could not try again.')
    }
  }

  useEffect(() => {
    if (view === 'page') return
    setText('')
    api.output(job.id, view).then(setText).catch((e: Error) => setText(e.message))
  }, [job.id, view, isLive])

  const copy = async () => {
    await navigator.clipboard.writeText(shareUrl ?? '')
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="mx-auto min-h-screen max-w-[1240px] px-5 sm:px-8">
      <header className="flex h-20 items-center justify-between">
        <Logo onClick={onHome} />
        <button onClick={onHome} className="rounded-full border border-line px-4 py-2 text-sm font-medium transition hover:border-signal">New video</button>
      </header>

      <main className="rise grid grid-cols-1 gap-10 pb-14 pt-4 lg:grid-cols-[.72fr_1.28fr]">
        <section className="min-w-0">
          <p className={`font-mono text-xs uppercase tracking-[.16em] ${launch?.state === 'failed' ? 'text-bad' : 'text-signal'}`}>
            {running ? '● Publishing' : isLive ? '● Live on the web' : launch?.state === 'failed' ? '● Not live' : '● Ready on this computer'}
          </p>
          <h1 className="mt-4 font-serif text-[clamp(46px,5.5vw,72px)] leading-[.96] tracking-[-.02em]">
            {running ? 'Going live.' : isLive ? 'Your page is live.' : launch?.state === 'failed' ? "It didn't go live." : 'Your source page is ready.'}
          </h1>
          <p className="mt-5 leading-relaxed text-soft">{job.draft?.question}</p>

          {running && (
            <ol aria-live="polite" className="mt-7 rounded-xl border border-line bg-panel px-4 py-2 text-sm">
              {STEPS.map(([key, label], i) => (
                <li key={key} className={`flex items-center gap-3 py-2 ${i === stepIndex ? 'text-paper' : i < stepIndex ? 'text-soft' : 'text-faint'}`}>
                  <span aria-hidden className="w-4 font-mono text-signal">{i < stepIndex ? '✓' : i === stepIndex ? '●' : ''}</span>{label}
                </li>
              ))}
            </ol>
          )}

          {launch?.state === 'failed' && (
            <div role="alert" className="mt-7 rounded-xl border border-bad/40 bg-bad/10 p-4 text-sm">
              <p>{launch.error}</p>
              <button onClick={retry} className="mt-3 rounded-full bg-signal px-4 py-2 font-semibold text-ink">Try again</button>
            </div>
          )}
          {retryError && <p role="alert" className="mt-3 text-sm text-bad">{retryError}</p>}

          {!running && (
            <div className="mt-7 flex items-center gap-2 rounded-xl border border-line bg-panel p-2 pl-3">
              <code className="min-w-0 flex-1 truncate font-mono text-xs text-soft">{shareUrl}</code>
              <button onClick={copy} className="rounded-lg bg-signal px-3 py-1.5 text-sm font-semibold text-ink">{copied ? 'Copied' : 'Copy'}</button>
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <a href={shareUrl ?? path} target="_blank" rel="noreferrer" className="font-medium text-signal hover:underline">Open page ↗</a>
            <a href={job.meta.source_url} target="_blank" rel="noreferrer" className="text-soft hover:text-paper">Original video ↗</a>
            {!running && <button onClick={() => onEdit({ ...job, status: 'review' })} className="text-soft hover:text-paper">Edit</button>}
            {submitFailed && <button onClick={retry} className="font-medium text-signal hover:underline">Retry submission</button>}
          </div>

          {isLive && !!launch?.targets.length && (
            <div className="mt-8">
              <h2 className="text-sm font-semibold">Indexing</h2>
              <ul className="mt-2 border-t border-line text-sm">
                {launch.targets.map((target) => (
                  <li key={target.name} className="border-b border-line py-3">
                    <div className="flex justify-between gap-4"><span className="font-medium">{target.name}</span><span className={`shrink-0 text-xs font-semibold ${TARGET_STYLE[target.status].className}`}>{TARGET_STYLE[target.status].label}</span></div>
                    <p className="mt-1 text-xs leading-relaxed text-faint">{target.detail}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <dl className="mt-8 border-t border-line text-sm">
            {[
              ['Claims with timestamps', String(job.draft?.claims.length ?? 0)],
              ['Credited to', job.meta.creator],
              ['Approved', job.approved_at ?? ''],
            ].map(([term, value]) => (
              <div key={term} className="flex justify-between gap-4 border-b border-line py-3"><dt className="text-faint">{term}</dt><dd className="text-right font-medium">{value}</dd></div>
            ))}
          </dl>
          <p className="mt-4 text-xs leading-relaxed text-faint">
            {isLive ? 'Submitted means the engine received the URL. It does not guarantee indexing or citation.' : launch?.state === 'not_configured' ? 'No public host is connected, so AI tools can’t reach this address yet.' : 'AI tools can read this page once it is live.'}
          </p>
        </section>

        <section className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel">
          <div role="tablist" aria-label="Output format" className="flex border-b border-line">
            {VIEWS.map((item) => (
              <button key={item.key} role="tab" aria-selected={view === item.key} onClick={() => setView(item.key)} className={`flex-1 px-3 py-3 text-sm font-medium transition ${view === item.key ? 'bg-raised text-paper shadow-[inset_0_-2px_0_var(--color-signal)]' : 'text-faint hover:text-paper'}`}>
                {item.label}
              </button>
            ))}
          </div>
          <p className="border-b border-line px-4 py-2 text-xs text-faint">{VIEWS.find((v) => v.key === view)?.note}</p>
          {view === 'page'
            ? <iframe title="Published page preview" src={shareUrl && isLive ? shareUrl : path} sandbox="" className="min-h-[640px] w-full flex-1 bg-paper" />
            : <pre className="h-[640px] flex-1 overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-[12.5px] leading-relaxed text-soft">{text || 'Loading…'}</pre>}
        </section>
      </main>
    </div>
  )
}
