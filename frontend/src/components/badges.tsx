import clsx from 'clsx'
import { Loader2 } from 'lucide-react'
import type { ClaimStatus, JobStatus } from '../types/api'
import { CLAIM_META } from './claimMeta'

const JOB: Record<JobStatus, string> = {
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  completed: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  failed: 'bg-rose-50 text-rose-700 ring-rose-200',
  cancelled: 'bg-amber-50 text-amber-700 ring-amber-200',
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ring-transparent', JOB[status])}>
      {status === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
      {status}
    </span>
  )
}

export function ClaimStatusBadge({ status }: { status: ClaimStatus }) {
  const m = CLAIM_META[status]
  return (
    <span className={clsx('inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', m.cls)}>
      <m.Icon className="h-3 w-3" /> {m.label}
    </span>
  )
}

export function DepthBadge({ depth }: { depth: string }) {
  return <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-600">{depth}</span>
}
