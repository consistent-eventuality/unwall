import type { Job } from './api'
import { Logo } from './App'

const STAGES = [
  ['reading', 'Opening the video', 'Checking length and tracks'],
  ['listening', 'Listening', 'Transcribing what is said'],
  ['watching', 'Watching', 'Sampling frames for on-screen detail'],
  ['writing', 'Writing the source page', 'Grounding each claim in a timestamp'],
] as const

export default function Processing({ job, jobId, onHome }: { job: Job | null; jobId: string; onHome: () => void }) {
  const failed = job?.status === 'failed'
  const current = Math.max(0, STAGES.findIndex(([key]) => key === job?.status))

  return (
    <div className="mx-auto flex min-h-screen max-w-[1120px] flex-col px-5 sm:px-8">
      <header className="flex h-20 items-center"><Logo onClick={onHome} /></header>
      <main className="grid flex-1 items-center gap-10 pb-14 md:grid-cols-[320px_1fr] md:gap-20">
        <div className="relative mx-auto aspect-[9/16] w-full max-w-[300px] overflow-hidden rounded-2xl border border-line bg-panel">
          <video src={`/api/jobs/${jobId}/video`} muted autoPlay loop playsInline className="h-full w-full object-cover" />
          {!failed && <div className="scanline" aria-hidden />}
        </div>

        <section aria-live="polite">
          <p className="font-mono text-xs uppercase tracking-[.16em] text-signal">{job?.filename ?? 'Preparing'}</p>
          <h1 className="mt-3 font-serif text-5xl leading-none sm:text-6xl">{failed ? 'That one didn’t work' : 'Reading your video'}</h1>
          {failed ? (
            <>
              <p role="alert" className="mt-5 max-w-md text-bad">{job?.error}</p>
              <button onClick={onHome} className="mt-6 rounded-full bg-signal px-5 py-2.5 font-semibold text-ink">Try another video</button>
            </>
          ) : (
            <ol className="mt-9 max-w-lg border-t border-line">
              {STAGES.map(([key, label, detail], i) => {
                const state = i < current ? 'done' : i === current ? 'now' : 'next'
                return (
                  <li key={key} className={`flex items-center gap-4 border-b border-line py-4 transition-opacity ${state === 'next' ? 'opacity-40' : ''}`}>
                    <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${state === 'done' ? 'bg-signal text-ink' : 'border border-line'}`}>
                      {state === 'done' ? '✓' : state === 'now' ? <i className="pulse-dot h-2 w-2 rounded-full bg-signal" /> : ''}
                    </span>
                    <span>
                      <span className="block font-medium">{label}</span>
                      <span className="block text-sm text-faint">{detail}</span>
                    </span>
                  </li>
                )
              })}
            </ol>
          )}
          {!!job?.frames.length && !failed && (
            <div className="mt-5 flex max-w-lg gap-1 overflow-hidden" aria-hidden>
              {job.frames.slice(0, 12).map((frame) => (
                <img key={frame.file} src={`/api/jobs/${jobId}/frames/${frame.file}`} alt="" className="rise h-16 w-9 rounded object-cover" />
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
