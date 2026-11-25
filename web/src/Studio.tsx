import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, stamp, type Claim, type ClaimSource, type Draft, type Job, type Meta } from './api'
import { Logo } from './App'

const SOURCE: Record<ClaimSource, { label: string; color: string }> = {
  speech: { label: 'Spoken', color: '#dcef78' },
  on_screen: { label: 'On screen', color: '#82d6e8' },
  visual: { label: 'Shown', color: '#f2b56b' },
}

const FIELDS: { key: keyof Meta; label: string; placeholder: string; required?: boolean; wide?: boolean }[] = [
  { key: 'creator', label: 'Creator name', placeholder: 'Sam Rivera', required: true, wide: true },
  { key: 'credential', label: 'Why trust them', placeholder: 'Restored 3,000+ pairs', wide: true },
  { key: 'source_url', label: 'Original video URL', placeholder: 'https://instagram.com/reel/…', required: true, wide: true },
  { key: 'profile_url', label: 'Profile URL', placeholder: 'https://instagram.com/you', wide: true },
  { key: 'platform', label: 'Platform', placeholder: 'Instagram' },
  { key: 'followers', label: 'Followers', placeholder: '412K' },
  { key: 'views', label: 'Video views', placeholder: '2.1M' },
  { key: 'stats_date', label: 'Stats from', placeholder: 'November 2026' },
]

export default function Studio({ job, onChange, onHome }: { job: Job; onChange: (job: Job) => void; onHome: () => void }) {
  const [draft, setDraft] = useState<Draft>(job.draft!)
  const [meta, setMeta] = useState<Meta>(job.meta)
  const [time, setTime] = useState(0)
  const [saving, setSaving] = useState<'saved' | 'saving' | 'error'>('saved')
  const [consent, setConsent] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const video = useRef<HTMLVideoElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<{ draft: Draft; meta: Meta } | null>(null)

  const inFlight = useRef<Promise<void>>(Promise.resolve())

  // Saves run one at a time so an older edit can never land after a newer one or after publish.
  const flush = useCallback(() => {
    window.clearTimeout(timer.current)
    const run = inFlight.current.catch(() => undefined).then(async () => {
      const next = pending.current
      if (!next) return
      pending.current = null
      setSaving('saving')
      try {
        await api.patch(job.id, next)
        setSaving(pending.current ? 'saving' : 'saved')
      } catch (e) {
        pending.current = pending.current ?? next
        setSaving('error')
        throw e
      }
    })
    inFlight.current = run
    return run
  }, [job.id])

  const queue = (nextDraft: Draft, nextMeta: Meta) => {
    setDraft(nextDraft)
    setMeta(nextMeta)
    pending.current = { draft: nextDraft, meta: nextMeta }
    setSaving('saving')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void flush().catch(() => undefined), 600)
  }

  useEffect(() => () => void flush().catch(() => undefined), [flush])

  const setClaim = (index: number, patch: Partial<Claim>) =>
    queue({ ...draft, claims: draft.claims.map((c, i) => (i === index ? { ...c, ...patch } : c)) }, meta)

  const seek = (seconds: number) => {
    if (!video.current) return
    video.current.currentTime = seconds
    void video.current.play().catch(() => undefined)
  }

  const activeIndex = useMemo(() => draft.claims.findIndex((c) => time >= c.start_s && time < Math.max(c.end_s, c.start_s + 0.5)), [draft.claims, time])
  const missing = [!meta.creator.trim() && 'creator name', !/^https?:\/\/\S+/.test(meta.source_url) && 'original video URL'].filter(Boolean) as string[]
  const canPublish = consent && !missing.length && !publishing && !!draft.claims.length

  const publish = async () => {
    setPublishing(true)
    setError(null)
    try {
      await flush()
      onChange(await api.publish(job.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Publishing failed.')
      setPublishing(false)
    }
  }

  return (
    <div className="min-h-screen">
      <header className="z-20 flex h-16 lg:sticky lg:top-0 items-center justify-between border-b border-line bg-ink/90 px-5 backdrop-blur sm:px-7">
        <Logo onClick={onHome} />
        <span className="font-mono text-xs text-faint" aria-live="polite">
          {saving === 'saving' ? 'Saving…' : saving === 'error' ? <span className="text-bad">Not saved</span> : 'Saved'}
        </span>
      </header>

      <div className="mx-auto grid max-w-[1500px] gap-8 px-5 py-7 sm:px-7 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_340px]">
        <aside className="lg:sticky lg:top-23 lg:self-start">
          <video ref={video} src={`/api/jobs/${job.id}/video`} controls playsInline onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} className="mx-auto max-h-[62vh] w-full max-w-[320px] rounded-xl border border-line bg-black object-contain" />
          <div className="mx-auto mt-4 max-w-[320px]">
            <div className="flex justify-between font-mono text-[11px] text-faint"><span>{stamp(time)}</span><span>{draft.claims.length} claims</span><span>{stamp(job.duration)}</span></div>
            <div className="relative mt-2 h-8 rounded bg-panel">
              {draft.claims.map((claim, i) => (
                <button
                  key={`${i}-${claim.start_s}`}
                  aria-label={`Jump to claim ${i + 1} at ${stamp(claim.start_s)}`}
                  onClick={() => seek(claim.start_s)}
                  className={`absolute top-1 bottom-1 min-w-1 rounded-sm transition-opacity ${activeIndex === i ? 'opacity-100' : 'opacity-45 hover:opacity-90'}`}
                  style={{ left: `${(claim.start_s / job.duration) * 100}%`, width: `${Math.max(1, ((claim.end_s - claim.start_s) / job.duration) * 100)}%`, background: SOURCE[claim.source].color }}
                />
              ))}
              <i className="pointer-events-none absolute -top-0.5 -bottom-0.5 w-px bg-paper" style={{ left: `${Math.min(100, (time / job.duration) * 100)}%` }} />
            </div>
            <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">
              {Object.values(SOURCE).map((s) => <span key={s.label} className="flex items-center gap-1.5 text-[11px] text-soft"><i className="h-2 w-2 rounded-sm" style={{ background: s.color }} />{s.label}</span>)}
            </div>
          </div>
        </aside>

        <main className="rise min-w-0">
          <p className="font-mono text-xs uppercase tracking-[.16em] text-signal">Review the source page</p>
          <label className="mt-3 block">
            <span className="sr-only">Question</span>
            <textarea rows={1} value={draft.question} onChange={(e) => queue({ ...draft, question: e.target.value }, meta)} className="ghost-edit font-serif text-[clamp(36px,4vw,54px)] leading-[1.03] tracking-[-.015em]" />
          </label>
          <label className="mt-5 block">
            <span className="sr-only">Answer</span>
            <textarea rows={3} value={draft.answer} onChange={(e) => queue({ ...draft, answer: e.target.value }, meta)} className="ghost-edit text-lg leading-relaxed text-paper/90" />
          </label>

          {!!draft.needs_review.length && (
            <div className="mt-6 rounded-xl border border-warn/30 bg-warn/[.07] p-4">
              <h2 className="text-sm font-semibold text-warn">Worth a second look</h2>
              <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-soft">{draft.needs_review.map((note) => <li key={note}>{note}</li>)}</ul>
            </div>
          )}

          <div className="mt-9 flex items-baseline justify-between border-b border-line pb-3">
            <h2 className="font-semibold">What the video shows</h2>
            <span className="text-xs text-faint">Click a time to verify it</span>
          </div>
          <ol>
            {draft.claims.map((claim, i) => (
              <li key={i} className={`group grid grid-cols-[62px_minmax(0,1fr)_24px] gap-3 border-b border-line py-3.5 transition-colors ${activeIndex === i ? 'bg-signal/[.06]' : ''}`}>
                <button onClick={() => seek(claim.start_s)} aria-label={`Play from ${stamp(claim.start_s)}`} className="self-start rounded px-1 py-0.5 text-left font-mono text-xs text-signal hover:bg-signal/15">
                  {stamp(claim.start_s)}
                </button>
                <div className="min-w-0">
                  <textarea rows={1} aria-label={`Claim ${i + 1}`} value={claim.text} onChange={(e) => setClaim(i, { text: e.target.value })} className="ghost-edit text-[15px] leading-snug" />
                  <p className="mt-1 flex gap-2 text-xs text-faint">
                    <span className="shrink-0 font-medium" style={{ color: SOURCE[claim.source].color }}>{SOURCE[claim.source].label}</span>
                    <span className="truncate" title={claim.evidence}>“{claim.evidence}”</span>
                  </p>
                </div>
                <button aria-label={`Remove claim ${i + 1}`} onClick={() => queue({ ...draft, claims: draft.claims.filter((_, n) => n !== i) }, meta)} className="h-6 rounded text-faint opacity-60 transition hover:bg-bad/15 hover:text-bad group-hover:opacity-100">×</button>
              </li>
            ))}
          </ol>

          <div className="mt-10 flex items-baseline justify-between border-b border-line pb-3">
            <h2 className="font-semibold">Questions to track</h2>
            <span className="text-xs text-faint">Asked to AI engines after it goes live</span>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-soft">After the page goes live, Unwall asks ChatGPT, Claude, and Perplexity these questions to see whether they cite it. Write them the way a person would ask.</p>
          <ul className="mt-3">
            <li className="flex gap-3 border-b border-line py-3 text-[15px]"><span className="w-16 shrink-0 font-mono text-xs leading-6 text-signal">Main</span><span className="min-w-0">{draft.question}</span></li>
            {draft.related_questions.map((q, i) => (
              <li key={i} className="group grid grid-cols-[64px_minmax(0,1fr)_24px] gap-3 border-b border-line py-3">
                <span className="font-mono text-xs leading-6 text-faint">Also</span>
                <input aria-label={`Tracked question ${i + 2}`} value={q} onChange={(e) => queue({ ...draft, related_questions: draft.related_questions.map((x, n) => (n === i ? e.target.value : x)) }, meta)} className="ghost-edit text-[15px]" />
                <button aria-label={`Remove tracked question ${i + 2}`} onClick={() => queue({ ...draft, related_questions: draft.related_questions.filter((_, n) => n !== i) }, meta)} className="h-6 rounded text-faint opacity-60 transition hover:bg-bad/15 hover:text-bad group-hover:opacity-100">×</button>
              </li>
            ))}
          </ul>
          {draft.related_questions.length < 4 && (
            <button onClick={() => queue({ ...draft, related_questions: [...draft.related_questions, ''] }, meta)} className="mt-3 text-sm font-medium text-signal hover:underline">+ Add a question</button>
          )}
        </main>

        <aside className="lg:col-span-2 xl:sticky xl:top-23 xl:col-span-1 xl:self-start">
          <div className="rounded-2xl border border-line bg-panel p-5">
            <h2 className="font-semibold">Credit line</h2>
            <p className="mt-1 text-xs leading-relaxed text-faint">Shown on the page and in its structured data.</p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              {FIELDS.map((field) => (
                <label key={field.key} className={field.wide ? 'col-span-2' : ''}>
                  <span className="mb-1 block text-xs text-soft">{field.label}{field.required && <span className="text-signal"> *</span>}</span>
                  <input
                    className="field"
                    value={meta[field.key]}
                    placeholder={field.placeholder}
                    onChange={(e) => {
                      const value = e.target.value
                      const next = { ...meta, [field.key]: value }
                      if (field.key === 'views') next.views_count = parseCount(value)
                      queue(draft, next)
                    }}
                  />
                </label>
              ))}
            </div>
            <label className="mt-5 flex gap-2.5 text-sm leading-snug text-soft">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0 accent-[#dcef78]" />
              I own this content or have permission, and I've checked these claims.
            </label>
            {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
            {!!missing.length && <p className="mt-3 text-xs text-warn">Add the {missing.join(' and ')}.</p>}
            <button onClick={publish} disabled={!canPublish} className="mt-4 w-full rounded-full bg-signal py-3 font-semibold text-ink transition hover:brightness-105 disabled:bg-raised disabled:text-faint">
              {publishing ? 'Publishing…' : 'Publish live'}
            </button>
          </div>
        </aside>
      </div>
    </div>
  )
}

function parseCount(value: string) {
  const match = value.trim().replace(/,/g, '').match(/^(\d+(?:\.\d+)?)([KMB])?$/i)
  if (!match) return ''
  const scale = { K: 1e3, M: 1e6, B: 1e9 }[match[2]?.toUpperCase() as 'K' | 'M' | 'B'] ?? 1
  return String(Math.round(Number(match[1]) * scale))
}
