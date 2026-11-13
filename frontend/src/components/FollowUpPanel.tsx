import { MessageSquare, Microscope, Send } from 'lucide-react'
import { useState } from 'react'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { Markdown } from './Markdown'
import { Button, Card, CardHeader, Spinner } from './ui'

export function FollowUpPanel({ jobId, enabled, onDeepen }: { jobId: string; enabled: boolean; onDeepen: () => void }) {
  const list = useAsync(() => api.followups(jobId), [jobId])
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [deep, setDeep] = useState('')
  const [deepening, setDeepening] = useState(false)
  const [err, setErr] = useState<string>()

  async function ask() {
    setAsking(true)
    setErr(undefined)
    try {
      const fu = await api.askFollowup(jobId, q)
      list.setData([...(list.data ?? []), fu])
      setQ('')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setAsking(false)
    }
  }

  async function deepen() {
    setDeepening(true)
    setErr(undefined)
    try {
      await api.deepen(jobId, deep)
      setDeep('')
      onDeepen()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setDeepening(false)
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader title="Ask follow-up questions" subtitle="Answers are grounded in this research's retrieved sources only, with citations." icon={<MessageSquare className="h-4 w-4" />} />
        <div className="space-y-4 p-5">
          {list.loading ? <Spinner /> : (list.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-400">No follow-up questions yet.</p>
          ) : (
            list.data!.map((f) => (
              <div key={f.id} className="space-y-2">
                <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-sm text-white">{f.question}</div>
                <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-slate-200 bg-slate-50 px-4 py-3">
                  {f.insufficient_evidence && <p className="mb-1 text-xs font-medium text-amber-600">Insufficient evidence in the collected sources</p>}
                  <Markdown>{f.answer}</Markdown>
                  {f.citations.length > 0 && (
                    <ul className="mt-2 space-y-0.5 border-t border-slate-200 pt-2 text-xs">
                      {f.citations.map((c) => (
                        <li key={c.label}>
                          <span className="font-semibold text-indigo-700">[{c.label}]</span>{' '}
                          {c.url ? <a href={c.url} target="_blank" rel="noopener noreferrer nofollow" className="text-slate-600 hover:underline">{c.title}</a> : c.title}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ))
          )}
          <div className="flex gap-2 pt-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && q.trim().length >= 3 && !asking && enabled && ask()}
              placeholder={enabled ? 'Ask about the findings…' : 'Available once research completes'}
              disabled={!enabled}
              className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none disabled:bg-slate-50"
            />
            <Button onClick={ask} loading={asking} disabled={!enabled || q.trim().length < 3}><Send className="h-4 w-4" /></Button>
          </div>
          {err && <p className="text-sm text-rose-600">{err}</p>}
        </div>
      </Card>
      <Card className="self-start">
        <CardHeader title="Request deeper investigation" subtitle="Runs another targeted research round, re-verifies, and produces a new report version." icon={<Microscope className="h-4 w-4" />} />
        <div className="space-y-3 p-5">
          <textarea value={deep} onChange={(e) => setDeep(e.target.value)} rows={3} disabled={!enabled}
            placeholder="e.g. battery recycling capacity and policy" className="w-full rounded-lg border border-slate-200 p-2.5 text-sm focus:border-indigo-400 focus:outline-none disabled:bg-slate-50" />
          <Button className="w-full" variant="secondary" onClick={deepen} loading={deepening} disabled={!enabled || deep.trim().length < 3}>Investigate further</Button>
        </div>
      </Card>
    </div>
  )
}
