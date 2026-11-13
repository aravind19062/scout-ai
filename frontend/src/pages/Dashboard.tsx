import { Activity, CheckCircle2, Coins, FileSearch, Plus, Search, ShieldCheck, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { DepthBadge, JobStatusBadge } from '../components/badges'
import { StageBreakdown } from '../components/StageBreakdown'
import { Button, Card, CardHeader, EmptyState, ErrorState, Spinner, Stat } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { JobStatus } from '../types/api'
import { fmtDuration, fmtInt, fmtUsd, timeAgo } from '../utils'

export function Dashboard() {
  const jobs = useAsync(() => api.listResearch(), [])
  const usage = useAsync(() => api.usage(), [])
  const nav = useNavigate()
  const [filter, setFilter] = useState<'all' | JobStatus>('all')
  const [q, setQ] = useState('')

  const rows = useMemo(
    () => (jobs.data ?? []).filter((j) => (filter === 'all' || j.status === filter) && j.question.toLowerCase().includes(q.toLowerCase())),
    [jobs.data, filter, q],
  )
  const running = (jobs.data ?? []).filter((j) => j.status === 'running' || j.status === 'queued').length
  const recentReports = (jobs.data ?? []).filter((j) => j.status === 'completed').slice(0, 4)

  async function remove(id: string) {
    if (!confirm('Delete this research session and all of its sources and reports?')) return
    await api.remove(id).catch((e) => alert(e.message))
    void jobs.reload()
    void usage.reload()
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Research dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">Plan, investigate, verify and report: autonomously, with citations.</p>
        </div>
        <Button onClick={() => nav('/new')}><Plus className="h-4 w-4" /> New Research</Button>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Research sessions" value={fmtInt(usage.data?.jobs)} icon={<FileSearch className="h-4 w-4" />} />
        <Stat label="Active now" value={running} icon={<Activity className="h-4 w-4" />} />
        <Stat
          label="Completed"
          value={fmtInt(usage.data?.by_status?.completed)}
          hint={usage.data?.per_completed_job.avg_elapsed_s != null ? `avg ${fmtDuration(usage.data.per_completed_job.avg_elapsed_s)} per report` : undefined}
          icon={<CheckCircle2 className="h-4 w-4" />}
        />
        <Stat
          label="Usage"
          value={fmtUsd(usage.data?.cost_usd)}
          hint={[
            usage.data?.per_completed_job.avg_cost_usd != null && `${fmtUsd(usage.data.per_completed_job.avg_cost_usd)} per report`,
            usage.data?.llm_cache_hit_rate != null && `${Math.round(usage.data.llm_cache_hit_rate * 100)}% LLM cache hits`,
            'estimate',
          ].filter(Boolean).join(' · ')}
          icon={<Coins className="h-4 w-4" />}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Research history"
            subtitle="All research sessions"
            action={
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2 top-2 h-3.5 w-3.5 text-slate-400" />
                  <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="w-36 rounded-md border border-slate-200 py-1.5 pl-7 pr-2 text-xs focus:border-indigo-400 focus:outline-none" />
                </div>
                <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} className="rounded-md border border-slate-200 px-2 py-1.5 text-xs">
                  {['all', 'running', 'completed', 'failed', 'cancelled', 'queued'].map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
            }
          />
          {jobs.loading ? <Spinner /> : jobs.error ? <div className="p-5"><ErrorState error={jobs.error} onRetry={jobs.reload} /></div> : rows.length === 0 ? (
            <EmptyState
              title={jobs.data?.length ? 'No sessions match your filter' : 'No research yet'}
              body={jobs.data?.length ? undefined : 'Start your first autonomous research session. SCOUT AI will plan, search, verify and write a cited report.'}
              action={!jobs.data?.length && <Button onClick={() => nav('/new')}><Plus className="h-4 w-4" /> New Research</Button>}
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {rows.map((j) => (
                <li key={j.id} className="group flex items-center gap-4 px-5 py-3.5 hover:bg-slate-50">
                  <Link to={`/research/${j.id}`} className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800 group-hover:text-indigo-700">{j.question}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <JobStatusBadge status={j.status} />
                      <DepthBadge depth={j.depth} />
                      {j.status === 'running' && <span>stage: {j.stage.replace('_', ' ')}</span>}
                      {j.halt_reason && <span className="text-amber-600">stopped: {j.halt_reason}</span>}
                      <span>{timeAgo(j.created_at)}</span>
                      <span>· {fmtUsd(j.usage?.cost_usd)}</span>
                    </div>
                  </Link>
                  <button onClick={() => remove(j.id)} className="rounded p-1.5 text-slate-300 opacity-0 transition hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100" aria-label="Delete" disabled={j.status === 'running'}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-6">
        <Card>
          <CardHeader title="Cost by agent" subtitle="All research sessions" icon={<Coins className="h-4 w-4" />} />
          <StageBreakdown stages={usage.data?.by_stage ?? {}} />
          {usage.data?.claims.supported_rate != null && (
            <p className="flex items-center gap-1.5 border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
              {Math.round(usage.data.claims.supported_rate * 100)}% of verified claims supported or partially supported by retrieved evidence
            </p>
          )}
        </Card>
        <Card>
          <CardHeader title="Recent reports" subtitle="Latest completed research" />
          {recentReports.length === 0 ? (
            <EmptyState title="No reports yet" body="Completed research reports will appear here." />
          ) : (
            <ul className="divide-y divide-slate-100">
              {recentReports.map((j) => (
                <li key={j.id}>
                  <Link to={`/research/${j.id}?tab=report`} className="block px-5 py-3 hover:bg-slate-50">
                    <p className="line-clamp-2 text-sm text-slate-700">{j.question}</p>
                    <p className="mt-1 text-xs text-slate-400">{j.completed_at ? timeAgo(j.completed_at) : ''}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        </div>
      </div>
    </div>
  )
}
