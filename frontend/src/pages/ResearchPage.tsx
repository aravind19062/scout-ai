import { Activity, AlertTriangle, ArrowLeft, Ban, BookOpen, Coins, ExternalLink, FileSearch, ListChecks, Play, RotateCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ActivityFeed } from '../components/ActivityFeed'
import { DepthBadge, JobStatusBadge } from '../components/badges'
import { StageBreakdown } from '../components/StageBreakdown'
import { ClaimList } from '../components/ClaimList'
import { FollowUpPanel } from '../components/FollowUpPanel'
import { ReportView } from '../components/ReportView'
import { SourceList } from '../components/SourceList'
import { StageStepper } from '../components/StageStepper'
import { TaskList } from '../components/TaskList'
import { Button, Card, CardHeader, EmptyState, ErrorState, Modal, Spinner, Stat, Tabs } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { useResearchEvents } from '../hooks/useResearchEvents'
import { api } from '../services/api'
import type { ProgressEvent, Report, Source } from '../types/api'
import { fmtDuration, fmtInt, fmtUsd } from '../utils'

type Tab = 'progress' | 'report' | 'verification' | 'sources' | 'followup'

export function ResearchPage() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const job = useAsync(() => api.getResearch(id), [id])
  const sources = useAsync(() => api.sources(id), [id])
  const claims = useAsync(() => api.claims(id), [id])
  const [report, setReport] = useState<Report>()
  const [reportErr, setReportErr] = useState<Error>()
  const [openSource, setOpenSource] = useState<string>()
  const [actionErr, setActionErr] = useState<string>()
  const [acting, setActing] = useState(false)

  const loadReport = useCallback(async () => {
    try {
      setReport(await api.report(id))
      setReportErr(undefined)
    } catch (e) {
      setReportErr(e as Error)
    }
  }, [id])

  // Coalesce bursts of progress events into one refresh per resource.
  const timers = useRef<Record<string, number>>({})
  const debounce = (key: string, fn: () => void, ms = 500) => {
    window.clearTimeout(timers.current[key])
    timers.current[key] = window.setTimeout(fn, ms)
  }
  const onEvent = useCallback((e: ProgressEvent) => {
    debounce('job', () => void job.reload())
    if (e.type === 'source_added') debounce('sources', () => void sources.reload())
    if (e.type === 'claim_verified' || e.type === 'analysis_done') debounce('claims', () => void claims.reload())
    if (e.type === 'report_ready' || e.type === 'section_regenerated') void loadReport()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  const stream = useResearchEvents(id, onEvent)

  const j = job.data
  const tab = (params.get('tab') as Tab) ?? (j?.status === 'completed' ? 'report' : 'progress')
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true })

  useEffect(() => {
    if (j && j.counts.reports > 0 && !report) void loadReport()
  }, [j, report, loadReport])

  if (job.loading && !j) return <Spinner label="Loading research…" />
  if (job.error) return <ErrorState error={job.error} onRetry={job.reload} />
  if (!j) return null

  const active = j.status === 'running' || j.status === 'queued'

  async function act(fn: () => Promise<unknown>, restart = false) {
    setActing(true)
    setActionErr(undefined)
    try {
      await fn()
      if (restart) stream.restart()
      await job.reload()
    } catch (e) {
      setActionErr((e as Error).message)
    } finally {
      setActing(false)
    }
  }

  const u = j.usage ?? {}
  const verified = Object.entries(j.counts.claims).filter(([k]) => k !== 'pending').reduce((a, [, n]) => a + (n ?? 0), 0)

  return (
    <div className="space-y-5">
      <Link to="/" className="no-print inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800"><ArrowLeft className="h-3.5 w-3.5" /> Dashboard</Link>

      <Card className="no-print p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <JobStatusBadge status={j.status} />
              <DepthBadge depth={j.depth} />
              {active && <span className="text-xs text-slate-400">{stream.connected ? '● live' : 'connecting…'}</span>}
            </div>
            <h1 className="text-lg font-semibold leading-snug text-slate-900">{j.question}</h1>
          </div>
          <div className="flex gap-2">
            {active && <Button variant="danger" size="sm" loading={acting} onClick={() => act(() => api.cancel(id))}><Ban className="h-3.5 w-3.5" /> Cancel</Button>}
            {(j.status === 'failed' || j.status === 'cancelled') && (
              <Button size="sm" loading={acting} onClick={() => act(() => api.resume(id), true)}><Play className="h-3.5 w-3.5" /> Resume from {j.stage.replace('_', ' ')}</Button>
            )}
          </div>
        </div>
        <div className="mt-4"><StageStepper stage={j.stage} status={j.status} iteration={j.iteration} /></div>
        {j.status === 'failed' && j.error && (
          <div className="mt-4 flex gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"><AlertTriangle className="h-4 w-4 shrink-0" /> {j.error}</div>
        )}
        {j.status === 'cancelled' && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">Research was cancelled. Evidence gathered so far is preserved; you can resume.</div>}
        {j.halt_reason && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">An execution limit was reached (<b>{j.halt_reason}</b>). The report was written from the evidence gathered up to that point.</div>}
        {actionErr && <p className="mt-3 text-sm text-rose-600">{actionErr}</p>}
      </Card>

      <div className="no-print grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Tasks" value={`${j.tasks.filter((t) => t.status === 'completed').length}/${j.tasks.length}`} />
        <Stat label="Sources" value={j.counts.sources} />
        <Stat label="Findings" value={j.counts.findings} />
        <Stat label="Claims verified" value={verified} />
        <Stat label="Cost (est.)" value={fmtUsd(u.cost_usd)} hint={`${fmtInt(u.total_tokens)} tokens · ${fmtDuration(u.elapsed_s)}`} />
      </div>

      <div className="no-print">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'progress', label: <span className="flex items-center gap-1.5"><Activity className="h-4 w-4" /> Live research</span> },
            { key: 'report', label: <span className="flex items-center gap-1.5"><FileSearch className="h-4 w-4" /> Report</span>, disabled: j.counts.reports === 0 },
            { key: 'verification', label: <span className="flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" /> Verification</span> },
            { key: 'sources', label: <span className="flex items-center gap-1.5"><BookOpen className="h-4 w-4" /> Sources ({j.counts.sources})</span> },
            { key: 'followup', label: <span className="flex items-center gap-1.5"><RotateCw className="h-4 w-4" /> Follow-up</span>, disabled: j.counts.reports === 0 },
          ]}
        />
      </div>

      {tab === 'progress' && (
        <div className="grid gap-5 lg:grid-cols-5">
          <div className="space-y-5 lg:col-span-3">
            <Card>
              <CardHeader title="Research plan" subtitle={`${j.tasks.length} tasks${j.iteration ? ` · ${j.iteration + 1} rounds` : ''}`} icon={<ListChecks className="h-4 w-4" />} />
              <TaskList tasks={j.tasks} />
            </Card>
            <Card>
              <CardHeader title="Verification results" subtitle="Claims checked against retrieved evidence" icon={<ShieldCheck className="h-4 w-4" />}
                action={claims.data?.length ? <Button variant="ghost" size="sm" onClick={() => setTab('verification')}>Details</Button> : undefined} />
              <ClaimList claims={claims.data ?? []} sources={sources.data ?? []} compact />
            </Card>
          </div>
          <div className="space-y-5 lg:col-span-2">
            <Card>
              <CardHeader title="Activity" subtitle="Execution log: tools used, decisions and results" icon={<Activity className="h-4 w-4" />} />
              <ActivityFeed events={stream.events} live={active} />
            </Card>
            {u.by_stage && Object.keys(u.by_stage).length > 0 && (
              <Card>
                <CardHeader title="Cost by agent" subtitle="Estimated spend and time attributed to each stage" icon={<Coins className="h-4 w-4" />} />
                <StageBreakdown stages={u.by_stage} />
              </Card>
            )}
            {(j.analysis?.gaps?.length ?? 0) > 0 && (
              <Card>
                <CardHeader title="Evidence gaps identified" />
                <ul className="list-disc space-y-1 px-9 py-3 text-xs text-slate-600">{j.analysis.gaps!.map((g) => <li key={g.description}>{g.description}</li>)}</ul>
              </Card>
            )}
          </div>
        </div>
      )}

      {tab === 'report' && (report ? <ReportView jobId={id} report={report} onReport={setReport} />
        : reportErr ? <ErrorState error={reportErr} onRetry={loadReport} /> : <Spinner label="Loading report…" />)}

      {tab === 'verification' && (
        <Card>
          <CardHeader title="Claim verification" subtitle="Each claim is checked against passages from the retrieved sources. Quotes are verified verbatim; statuses are assigned by deterministic rules, not model confidence." />
          {claims.loading ? <Spinner /> : <ClaimList claims={claims.data ?? []} sources={sources.data ?? []} onOpenSource={setOpenSource} />}
        </Card>
      )}

      {tab === 'sources' && (
        <Card>
          <CardHeader title="Sources" subtitle="Every document retrieved and processed for this research" />
          {sources.loading ? <Spinner /> : sources.data?.length ? <SourceList sources={sources.data} onOpen={setOpenSource} /> : <EmptyState title="No sources yet" />}
        </Card>
      )}

      {tab === 'followup' && <FollowUpPanel jobId={id} enabled={j.status === 'completed'} onDeepen={() => { stream.restart(); setReport(undefined); void job.reload(); setTab('progress') }} />}

      <SourceModal jobId={id} sourceId={openSource} onClose={() => setOpenSource(undefined)} />
    </div>
  )
}

function SourceModal({ jobId, sourceId, onClose }: { jobId: string; sourceId?: string; onClose: () => void }) {
  // Results are tagged with the source they were fetched for, so switching sources shows the
  // spinner (stale result is ignored) without resetting state synchronously in the effect.
  const [result, setResult] = useState<{ sourceId: string; src?: Source; err?: string }>()
  useEffect(() => {
    if (!sourceId) return
    let cancelled = false
    api
      .source(jobId, sourceId)
      .then((src) => !cancelled && setResult({ sourceId, src }))
      .catch((e) => !cancelled && setResult({ sourceId, err: e.message }))
    return () => {
      cancelled = true
    }
  }, [jobId, sourceId])
  const current = result?.sourceId === sourceId ? result : undefined
  const src = current?.src
  const err = current?.err
  return (
    <Modal open={!!sourceId} onClose={onClose} wide title={src?.title ?? 'Source'}>
      {err ? <p className="text-sm text-rose-600">{err}</p> : !src ? <Spinner /> : (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            <span>{src.domain ?? src.source_type}</span><span>published {src.published_at ?? 'unknown'}</span>
            <span>retrieved {new Date(src.retrieved_at).toLocaleString()}</span><span>extracted with {src.extraction_method}</span>
            {src.url && <a href={src.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-indigo-600 hover:underline">Open original <ExternalLink className="h-3 w-3" /></a>}
          </div>
          {src.security_flags.length > 0 && <p className="rounded bg-amber-50 px-3 py-2 text-xs text-amber-700">This source contains text resembling instructions to an AI ({src.security_flags.join(', ')}). It was treated strictly as data.</p>}
          <p className="text-[11px] uppercase tracking-wide text-slate-400">Extracted text (as processed by SCOUT AI)</p>
          <pre className="max-h-[55vh] overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-4 font-sans text-xs leading-relaxed text-slate-700">{src.text}</pre>
        </div>
      )}
    </Modal>
  )
}
