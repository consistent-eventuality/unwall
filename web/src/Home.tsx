import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, type Sample } from './api'
import { Logo } from './App'

export default function Home({ onStart }: { onStart: (id: string) => void }) {
  const [samples, setSamples] = useState<Sample[]>([])
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.samples().then(setSamples).catch(() => setError("Can't reach the local server."))
  }, [])

  const run = async (label: string, action: () => Promise<{ id: string }>) => {
    setBusy(label)
    setError(null)
    try {
      onStart((await action()).id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
      setBusy(null)
    }
  }

  const take = (file?: File) => {
    if (file) run('upload', () => api.upload(file))
  }

  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    take(event.dataTransfer.files[0])
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-[1240px] flex-col px-5 sm:px-8">
      <header className="flex h-20 items-center justify-between">
        <Logo onClick={() => undefined} />
        <span className="font-mono text-xs text-faint">Your content. Your opt-in.</span>
      </header>

      <main className="grid flex-1 items-center gap-12 pb-14 pt-6 lg:grid-cols-[1.08fr_.92fr] lg:gap-16">
        <section className="rise">
          <p className="mb-5 font-mono text-xs uppercase tracking-[.16em] text-signal">Video to AI source page</p>
          <h1 className="font-serif text-[clamp(54px,8vw,104px)] leading-[.92] tracking-[-.025em]">
            Your best work is <em className="text-signal">invisible</em> to AI.
          </h1>
          <p className="mt-7 max-w-[520px] text-lg leading-relaxed text-soft">
            Platforms keep AI out. You can let it in, on your terms. Drop in a video and get a source page that AI can read, cite, and link back to you.
          </p>
          <ol className="mt-9 grid max-w-[560px] grid-cols-3 border-t border-line pt-4 font-mono text-xs text-soft">
            {['Reads voice + visuals', 'You approve each claim', 'Credit stays with you'].map((item, i) => (
              <li key={item} className="pr-3 leading-relaxed"><span className="block text-signal">0{i + 1}</span>{item}</li>
            ))}
          </ol>
        </section>

        <section className="rise [animation-delay:90ms]">
          <div
            role="button"
            tabIndex={0}
            aria-label="Upload a video"
            onClick={() => !busy && input.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
            className={`grid h-56 place-items-center rounded-2xl border border-dashed text-center transition ${dragging ? 'border-signal bg-signal/10' : 'border-[#4a4e42] bg-panel hover:border-signal'}`}
          >
            <input ref={input} type="file" hidden accept=".mp4,.mov,.m4v,.webm,video/*" onChange={(e) => take(e.target.files?.[0])} />
            <div>
              <div className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-full bg-signal text-xl text-ink" aria-hidden>↑</div>
              <p className="text-lg font-semibold">{busy === 'upload' ? 'Uploading…' : 'Drop your video'}</p>
              <p className="mt-1 text-sm text-faint">MP4, MOV, or WebM up to 500 MB</p>
            </div>
          </div>

          {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}

          <div className="mt-7 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold">Or try a sample</h2>
            <span className="font-mono text-[11px] text-faint">fictional creator</span>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            {samples.map((sample) => (
              <button
                key={sample.id}
                aria-label={`Try sample: ${sample.title}`}
                disabled={!!busy}
                onClick={() => run(sample.id, () => api.startSample(sample.id))}
                className="group overflow-hidden rounded-xl border border-line bg-panel text-left transition hover:-translate-y-0.5 hover:border-signal disabled:opacity-50"
              >
                <video
                  src={`/api/samples/${sample.id}/poster#t=2.5`}
                  muted
                  playsInline
                  preload="metadata"
                  onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
                  onMouseLeave={(e) => e.currentTarget.pause()}
                  aria-label={sample.title}
                  className="aspect-[9/14] w-full object-cover object-top"
                />
                <span className="block min-h-[66px] p-2.5">
                  <span className="block font-mono text-[10px] uppercase tracking-wider text-signal">{busy === sample.id ? 'Starting…' : sample.kind}</span>
                  <span className="mt-1 block text-xs leading-snug text-soft">{sample.note}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      </main>
    </div>
  )
}
