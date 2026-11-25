import { useCallback, useEffect, useState } from 'react'
import { api, type Job, type Results as ResultsData } from './api'

const ENGINES = ['ChatGPT', 'Claude', 'Perplexity']

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

export default function Results({ job }: { job: Job }) {
  const [data, setData] = useState<ResultsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncNote, setSyncNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.results(job.id))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load results.')
    }
  }, [job.id])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!data?.checking) return
    const timer = window.setTimeout(load, 2500)
    return () => window.clearTimeout(timer)
  }, [data, load])

  const check = async () => {
    setError(null)
    try {
      setData(await api.check(job.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start a check.')
    }
  }

  const sync = async () => {
    setSyncing(true)
    setSyncNote(null)
    try {
      const outcome = await api.sync()
      setSyncNote(outcome.errors.length ? outcome.errors[0] : `Synced. ${outcome.new_hits} new ${outcome.new_hits === 1 ? 'entry' : 'entries'} from ${outcome.requests} requests.`)
      await load()
    } catch (e) {
      setSyncNote(e instanceof Error ? e.message : 'Could not sync visits.')
    } finally {
      setSyncing(false)
    }
  }

  if (!data) return error ? <p role="alert" className="mt-8 text-sm text-bad">{error}</p> : null

  const latest = data.latest
  const crawlers = Object.entries(data.crawlers)
  const unconfirmed = data.fetches.total - data.fetches.trusted
  const stats = [
    { label: 'AI fetches', value: data.fetches.trusted, note: unconfirmed ? `${unconfirmed} more couldn't be confirmed` : 'pulled for someone’s question' },
    {
      label: 'Cited',
      value: latest ? `${latest.page_cited}/${latest.asked}` : '—',
      note: !latest ? 'no checks yet'
        : latest.label === 'Baseline' ? 'answers at baseline'
        : `answers in ${latest.label.toLowerCase()}${data.baseline ? ` · baseline ${data.baseline.page_cited}/${data.baseline.asked}` : ''}`,
    },
    { label: 'Clicks to post', value: data.clicks, note: Object.keys(data.referrals).length ? `from ${Object.entries(data.referrals).map(([k, v]) => `${k} ${v}`).join(', ')}` : 'through Watch the original' },
  ]

  return (
    <section className="mt-9" aria-labelledby="results-title">
      <div className="flex items-baseline justify-between">
        <h2 id="results-title" className="text-sm font-semibold">Results</h2>
        <a href={`/report/${job.id}`} target="_blank" rel="noreferrer" className="text-xs font-medium text-signal hover:underline">Creator report ↗</a>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {stats.map((s) => (
          <div key={s.label} className="min-w-0 rounded-xl border border-line bg-panel p-3">
            <p className="text-[11px] text-faint">{s.label}</p>
            <p className="mt-1 font-serif text-3xl leading-none tabular-nums">{s.value}</p>
            <p className="mt-1.5 text-[11px] leading-snug text-faint">{s.note}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-faint"><th className="py-2 pr-2 font-medium">Check</th>{ENGINES.map((e) => <th key={e} className="py-2 pr-2 font-medium">{e}</th>)}</tr>
          </thead>
          <tbody>
            {data.checks.length === 0 && (
              <tr><td colSpan={4} className="border-t border-line py-3 text-faint">{data.checking ? 'The baseline is running now.' : 'No checks yet. The baseline runs right after the page goes live.'}</td></tr>
            )}
            {data.checks.map((c) => (
              <tr key={c.run_id} className="border-t border-line align-top">
                <td className="py-2.5 pr-2"><span className="font-medium text-paper">{c.label}</span><br /><span className="text-faint">{day(c.at)}</span></td>
                {ENGINES.map((name) => {
                  const e = c.engines[name]
                  if (!e) return <td key={name} className="py-2.5 pr-2 text-faint">—</td>
                  if (!e.asked) return <td key={name} className="py-2.5 pr-2 text-faint" title={e.note ?? ''}>{e.skipped ? 'Skipped' : 'Failed'}</td>
                  return (
                    <td key={name} className="py-2.5 pr-2 tabular-nums">
                      <span className={e.page_cited ? 'font-semibold text-signal' : 'text-soft'}>page {e.page_cited}/{e.asked}</span>
                      <br /><span className="text-faint">post {e.post_cited} · searched {e.searched}</span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {crawlers.length > 0 && <p className="mt-3 text-xs text-faint">Crawled by {crawlers.map(([k, v]) => `${k} (${v})`).join(', ')}.</p>}
      {error && <p role="alert" className="mt-3 text-xs text-bad">{error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button onClick={check} disabled={data.checking} className="rounded-full border border-line px-3.5 py-1.5 text-xs font-medium transition hover:border-signal disabled:text-faint">
          {data.checking ? 'Checking…' : 'Check citations now'}
        </button>
        <button onClick={sync} disabled={syncing} className="rounded-full border border-line px-3.5 py-1.5 text-xs font-medium transition hover:border-signal disabled:text-faint">
          {syncing ? 'Syncing…' : 'Sync visits'}
        </button>
        <span className="text-[11px] text-faint" aria-live="polite">{syncNote ?? (data.last_sync ? `Last synced ${day(data.last_sync.at)}` : 'Visits not synced yet')}</span>
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-faint">Each check asks {data.questions.length} question{data.questions.length === 1 ? '' : 's'} and tells the engine to search first. A check costs a few cents per engine.</p>
    </section>
  )
}
