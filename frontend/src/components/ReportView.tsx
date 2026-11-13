import { Download, Printer, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useDemo } from '../hooks/useDemo'
import { api } from '../services/api'
import type { ClaimStatus, Report } from '../types/api'
import { ClaimStatusBadge } from './badges'
import { CLAIM_META } from './claimMeta'
import { Markdown } from './Markdown'
import { Button, Card } from './ui'

export function ReportView({ jobId, report, onReport }: { jobId: string; report: Report; onReport: (r: Report) => void }) {
  const [busy, setBusy] = useState<string>()
  const [err, setErr] = useState<string>()
  const demo = useDemo()
  const stats = report.citation_stats
  const claimCounts = Object.entries(stats.claims_by_status ?? {}) as [ClaimStatus, number][]

  async function regenerate(key: string) {
    setBusy(key)
    setErr(undefined)
    try {
      onReport({ ...(await api.regenerate(jobId, key)), versions: [...report.versions, report.version + 1] })
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(undefined)
    }
  }

  async function switchVersion(v: number) {
    try {
      onReport(await api.report(jobId, v))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
      <aside className="no-print space-y-4 lg:sticky lg:top-4 lg:self-start">
        <Card className="p-3">
          <div className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Contents</div>
          <nav className="flex flex-col">
            {report.sections.map((s, i) => (
              <a key={s.key} href={`#sec-${s.key}`} className="rounded px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 hover:text-indigo-700">
                {i + 1}. {s.title}
              </a>
            ))}
          </nav>
        </Card>
        <Card className="space-y-2 p-4 text-xs">
          <div className="font-semibold text-slate-700">Verification</div>
          {claimCounts.length === 0 && <p className="text-slate-400">No claims verified.</p>}
          {claimCounts.map(([k, n]) => (
            <div key={k} className="flex items-center justify-between"><ClaimStatusBadge status={k} /><span className="tabular-nums text-slate-600">{n}</span></div>
          ))}
          <div className="border-t border-slate-100 pt-2 text-slate-500">
            {stats.unique_sources_cited ?? 0} sources cited · {stats.citations_in_text ?? 0} inline citations
            {!!stats.invalid_citations_removed && <div className="text-amber-600">{stats.invalid_citations_removed} invalid citation(s) removed</div>}
          </div>
        </Card>
      </aside>

      <div className="min-w-0 space-y-4">
        <div className="no-print flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            Version
            <select value={report.version} onChange={(e) => switchVersion(Number(e.target.value))} className="rounded-md border border-slate-200 px-2 py-1">
              {report.versions.map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
            <span>· {new Date(report.created_at).toLocaleString()}</span>
          </div>
          <div className="flex gap-2">
            {(['md', 'html', 'json'] as const).map((f) => (
              <a key={f} href={api.exportUrl(jobId, f, report.version)}>
                <Button variant="secondary" size="sm"><Download className="h-3.5 w-3.5" /> {f.toUpperCase()}</Button>
              </a>
            ))}
            <Button variant="secondary" size="sm" onClick={() => window.print()}><Printer className="h-3.5 w-3.5" /> PDF</Button>
          </div>
        </div>
        {err && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{err}</div>}

        <Card className="px-6 py-6 sm:px-10">
          <h1 className="mb-6 text-xl font-semibold text-slate-900">{report.title}</h1>
          {report.sections.map((s) => (
            <section key={s.key} id={`sec-${s.key}`} className="group mb-8 scroll-mt-4">
              <div className="mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
                <h2 className="text-base font-semibold text-slate-900">{s.title}</h2>
                {demo?.enabled ? null : s.generated_by === 'llm' ? (
                  <Button variant="ghost" size="sm" className="no-print opacity-0 group-hover:opacity-100" loading={busy === s.key} onClick={() => regenerate(s.key)}>
                    <RefreshCw className="h-3 w-3" /> Regenerate
                  </Button>
                ) : (
                  <span className="no-print text-[10px] uppercase tracking-wide text-slate-300" title="Generated from recorded data, not by the model">system-generated</span>
                )}
              </div>
              <Markdown refs={report.references}>{s.markdown}</Markdown>
            </section>
          ))}
        </Card>
        <p className="no-print text-center text-[11px] text-slate-400">
          Status legend: {Object.values(CLAIM_META).map((m) => m.label).join(' · ')}
        </p>
      </div>
    </div>
  )
}
