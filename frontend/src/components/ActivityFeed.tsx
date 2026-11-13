import clsx from 'clsx'
import {
  AlertTriangle, Ban, BookOpen, Brain, CheckCircle2, CircleDot, Copy, FileWarning, GitBranch, ListChecks, Search, ShieldCheck, XCircle,
} from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { ProgressEvent } from '../types/api'

const ICONS: Record<string, { Icon: typeof Search; cls: string }> = {
  search: { Icon: Search, cls: 'text-sky-500' },
  source_added: { Icon: BookOpen, cls: 'text-emerald-500' },
  source_failed: { Icon: FileWarning, cls: 'text-amber-500' },
  source_duplicate: { Icon: Copy, cls: 'text-slate-400' },
  search_failed: { Icon: XCircle, cls: 'text-rose-500' },
  plan_created: { Icon: ListChecks, cls: 'text-indigo-500' },
  tasks_added: { Icon: GitBranch, cls: 'text-indigo-500' },
  task_completed: { Icon: CheckCircle2, cls: 'text-emerald-500' },
  task_failed: { Icon: XCircle, cls: 'text-rose-500' },
  task_retry: { Icon: AlertTriangle, cls: 'text-amber-500' },
  analysis_done: { Icon: Brain, cls: 'text-violet-500' },
  claim_verified: { Icon: ShieldCheck, cls: 'text-teal-500' },
  decision: { Icon: GitBranch, cls: 'text-indigo-500' },
  halted: { Icon: AlertTriangle, cls: 'text-amber-500' },
  job_failed: { Icon: XCircle, cls: 'text-rose-500' },
  job_cancelled: { Icon: Ban, cls: 'text-amber-500' },
  job_completed: { Icon: CheckCircle2, cls: 'text-emerald-600' },
}

export function ActivityFeed({ events, live }: { events: ProgressEvent[]; live: boolean }) {
  const end = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = box.current
    if (live && el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) end.current?.scrollIntoView({ block: 'end' })
  }, [events.length, live])

  const shown = events.filter((e) => e.type !== 'stage')
  return (
    <div ref={box} className="max-h-[520px] overflow-y-auto px-5 py-3">
      {shown.length === 0 && <p className="py-6 text-center text-sm text-slate-400">Waiting for activity…</p>}
      <ol className="space-y-2">
        {shown.map((e) => {
          const meta = ICONS[e.type] ?? { Icon: CircleDot, cls: 'text-slate-400' }
          return (
            <li key={e.id} className="flex gap-2.5 text-sm">
              <meta.Icon className={clsx('mt-0.5 h-4 w-4 shrink-0', meta.cls)} />
              <div className="min-w-0 flex-1">
                <p className={clsx('break-words text-slate-700', e.type.startsWith('job_') && 'font-medium')}>{e.message}</p>
                {e.type === 'source_added' && Array.isArray(e.data.security_flags) && e.data.security_flags.length > 0 && (
                  <p className="text-xs text-amber-600">⚠ Possible prompt-injection text detected; treated as data only.</p>
                )}
              </div>
              <time className="shrink-0 text-[11px] tabular-nums text-slate-400">{new Date(e.created_at).toLocaleTimeString()}</time>
            </li>
          )
        })}
      </ol>
      <div ref={end} />
    </div>
  )
}
