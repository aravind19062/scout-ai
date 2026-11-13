import clsx from 'clsx'
import { CheckCircle2, CircleDashed, CircleSlash, HelpCircle, Loader2, ShieldAlert, XCircle } from 'lucide-react'
import type { ClaimStatus, JobStatus } from '../types/api'

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

export const CLAIM_META: Record<ClaimStatus, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  supported: { label: 'Supported', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200', Icon: CheckCircle2 },
  partially_supported: { label: 'Partially supported', cls: 'bg-lime-50 text-lime-700 ring-lime-200', Icon: CircleDashed },
  conflicting: { label: 'Conflicting evidence', cls: 'bg-amber-50 text-amber-700 ring-amber-200', Icon: ShieldAlert },
  unsupported: { label: 'Unsupported', cls: 'bg-rose-50 text-rose-700 ring-rose-200', Icon: XCircle },
  unable_to_verify: { label: 'Unable to verify', cls: 'bg-slate-100 text-slate-600 ring-slate-200', Icon: HelpCircle },
  pending: { label: 'Not verified', cls: 'bg-slate-50 text-slate-500 ring-slate-200', Icon: CircleSlash },
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
