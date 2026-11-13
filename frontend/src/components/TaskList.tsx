import clsx from 'clsx'
import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import type { Task } from '../types/api'

export function TaskList({ tasks }: { tasks: Task[] }) {
  if (!tasks.length) return <p className="px-5 py-6 text-center text-sm text-slate-400">The research plan will appear here.</p>
  return (
    <ul className="divide-y divide-slate-100">
      {tasks.map((t) => (
        <li key={t.id} className="flex gap-3 px-5 py-3">
          <div className="mt-0.5">
            {t.status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              : t.status === 'running' ? <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
              : t.status === 'failed' ? <XCircle className="h-4 w-4 text-rose-500" />
              : <Circle className="h-4 w-4 text-slate-300" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-slate-400">{t.key}</span>
              <span className="text-sm text-slate-800">{t.objective}</span>
              {t.origin !== 'plan' && (
                <span className={clsx('rounded px-1.5 py-px text-[10px] font-semibold uppercase', t.origin === 'gap' ? 'bg-violet-50 text-violet-600' : 'bg-sky-50 text-sky-600')}>
                  {t.origin === 'gap' ? `gap · round ${t.iteration + 1}` : 'follow-up'}
                </span>
              )}
              <span className="text-[10px] text-slate-400">P{t.priority}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {t.queries.map((q) => <span key={q} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">🔎 {q}</span>)}
            </div>
            {t.summary && <p className="mt-1.5 text-xs text-slate-500">{t.summary}</p>}
            {t.error && t.status === 'failed' && <p className="mt-1 text-xs text-rose-600">{t.error}</p>}
          </div>
        </li>
      ))}
    </ul>
  )
}
