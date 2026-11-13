import clsx from 'clsx'
import { FileText, Gauge, Rocket, Sparkles, Upload, X, Zap } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, CardHeader } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { Depth } from '../types/api'

const DEPTHS: { key: Depth; title: string; blurb: string; icon: typeof Zap }[] = [
  { key: 'quick', title: 'Quick Research', blurb: 'Fast overview, single research round.', icon: Zap },
  { key: 'standard', title: 'Standard Research', blurb: 'Balanced breadth with one gap-filling round.', icon: Gauge },
  { key: 'deep', title: 'Deep Research', blurb: 'Broad investigation, multiple verification rounds.', icon: Rocket },
]

const EXAMPLES = [
  'Analyse the Indian electric vehicle market. Identify major competitors, market size, growth trends, government policies, challenges, investment opportunities, and future outlook.',
  'What is the current state of solid-state battery commercialisation? Which companies lead, what are the technical barriers, and what timelines are credible?',
  'Compare the regulatory approaches to generative AI in the EU, US and China, and their implications for startups.',
]
const MOCK_EXAMPLE = 'Analyse the Freedonia electric vehicle market. Identify market size, major competitors, growth trends, government policy, battery technology developments, challenges, investment opportunities and future outlook.'

export function NewResearch() {
  const nav = useNavigate()
  const cfg = useAsync(() => api.config(), [])
  const docs = useAsync(() => api.documents(), [])
  const [question, setQuestion] = useState('')
  const [depth, setDepth] = useState<Depth>('standard')
  const [selected, setSelected] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string>()
  const fileRef = useRef<HTMLInputElement>(null)

  const examples = cfg.data?.mock_mode ? [MOCK_EXAMPLE, ...EXAMPLES] : EXAMPLES
  const valid = question.trim().length >= 10

  async function submit() {
    setSubmitting(true)
    setError(undefined)
    try {
      const job = await api.createResearch(question, depth, selected)
      nav(`/research/${job.id}`)
    } catch (e) {
      setError((e as Error).message)
      setSubmitting(false)
    }
  }

  async function onUpload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    setError(undefined)
    try {
      for (const f of Array.from(files)) {
        const d = await api.upload(f)
        setSelected((s) => (s.includes(d.id) ? s : [...s, d.id]))
      }
      await docs.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">New research</h1>
        <p className="mt-1 text-sm text-slate-500">Describe what you want investigated. Be specific about scope, geography and the aspects you care about.</p>
      </div>

      <Card>
        <CardHeader title="Research question" icon={<Sparkles className="h-4 w-4" />} />
        <div className="space-y-3 p-5">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={5}
            maxLength={2000}
            placeholder="e.g. Analyse the Indian electric vehicle market…"
            className="w-full resize-y rounded-lg border border-slate-200 p-3 text-sm leading-relaxed focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
          />
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>{question.trim().length < 10 ? 'At least 10 characters' : ' '}</span>
            <span>{question.length}/2000</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {examples.map((ex) => (
              <button key={ex} onClick={() => setQuestion(ex)} className="max-w-full truncate rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700" title={ex}>
                {ex.length > 70 ? ex.slice(0, 70) + '…' : ex}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Research depth" subtitle="Each level sets real limits on breadth, research rounds, verification effort and budget." />
        <div className="grid gap-3 p-5 sm:grid-cols-3">
          {DEPTHS.map(({ key, title, blurb, icon: Icon }) => {
            const d = cfg.data?.depths[key]
            return (
              <button
                key={key}
                onClick={() => setDepth(key)}
                className={clsx('rounded-xl border p-4 text-left transition', depth === key ? 'border-indigo-500 bg-indigo-50/50 ring-2 ring-indigo-500/20' : 'border-slate-200 hover:border-slate-300')}
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Icon className="h-4 w-4 text-indigo-600" /> {title}</div>
                <p className="mt-1 text-xs text-slate-500">{blurb}</p>
                {d && (
                  <dl className="mt-3 grid grid-cols-2 gap-x-2 gap-y-1 text-[11px] text-slate-500">
                    <dt>Tasks</dt><dd className="text-right font-medium text-slate-700">{d.max_tasks}</dd>
                    <dt>Rounds</dt><dd className="text-right font-medium text-slate-700">{d.max_iterations}</dd>
                    <dt>Claims verified</dt><dd className="text-right font-medium text-slate-700">≤ {d.max_claims}</dd>
                    <dt>Tool calls</dt><dd className="text-right font-medium text-slate-700">≤ {d.max_tool_calls}</dd>
                    <dt>Time limit</dt><dd className="text-right font-medium text-slate-700">{Math.round(Number(d.max_duration_s) / 60)} min</dd>
                    <dt>Budget</dt><dd className="text-right font-medium text-slate-700">${Number(d.max_cost_usd).toFixed(2)}</dd>
                  </dl>
                )}
              </button>
            )
          })}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Your documents (optional)"
          subtitle={`Add PDFs or text files as additional evidence. Max ${cfg.data?.upload.max_mb ?? 20} MB each.`}
          action={
            <>
              <input ref={fileRef} type="file" multiple accept=".pdf,.txt,.md,.html,.htm" className="hidden" onChange={(e) => onUpload(e.target.files)} />
              <Button variant="secondary" size="sm" loading={uploading} onClick={() => fileRef.current?.click()}><Upload className="h-3.5 w-3.5" /> Upload</Button>
            </>
          }
        />
        <div className="p-5">
          {!docs.data?.length ? (
            <p className="text-sm text-slate-400">No documents in your library yet.</p>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2">
              {docs.data.map((d) => {
                const on = selected.includes(d.id)
                return (
                  <li key={d.id}>
                    <button
                      onClick={() => setSelected((s) => (on ? s.filter((x) => x !== d.id) : [...s, d.id]))}
                      className={clsx('flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm', on ? 'border-indigo-400 bg-indigo-50' : 'border-slate-200 hover:bg-slate-50')}
                    >
                      <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                      <span className="flex-1 truncate">{d.title}</span>
                      {on && <X className="h-3.5 w-3.5 text-indigo-500" />}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </Card>

      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={() => nav('/')}>Cancel</Button>
        <Button onClick={submit} disabled={!valid} loading={submitting}><Rocket className="h-4 w-4" /> Start research</Button>
      </div>
    </div>
  )
}
