import clsx from 'clsx'
import { Check, Loader2 } from 'lucide-react'
import type { JobStatus } from '../types/api'

const STEPS = [
  { key: 'plan', label: 'Plan' },
  { key: 'research', label: 'Research' },
  { key: 'analyze', label: 'Analyse' },
  { key: 'verify', label: 'Verify' },
  { key: 'report', label: 'Report' },
]
// gap_plan sits between verify and the next research round; show it as part of "verify".
const ORDER: Record<string, number> = { plan: 0, research: 1, analyze: 2, verify: 3, gap_plan: 3, report: 4, done: 5 }

export function StageStepper({ stage, status, iteration }: { stage: string; status: JobStatus; iteration: number }) {
  const idx = ORDER[stage] ?? 0
  const active = status === 'running' || status === 'queued'
  return (
    <div className="flex items-center gap-1 overflow-x-auto">
      {STEPS.map((s, i) => {
        const done = i < idx || stage === 'done'
        const current = i === idx && stage !== 'done'
        return (
          <div key={s.key} className="flex items-center gap-1">
            <div
              className={clsx(
                'flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium',
                done && 'bg-emerald-50 text-emerald-700',
                current && active && 'bg-indigo-600 text-white',
                current && !active && 'bg-amber-50 text-amber-700',
                !done && !current && 'bg-slate-100 text-slate-400',
              )}
            >
              {done ? <Check className="h-3 w-3" /> : current && active ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className="w-3 text-center">{i + 1}</span>}
              {s.label}
              {s.key === 'research' && iteration > 0 && <span className="opacity-75">· round {iteration + 1}</span>}
            </div>
            {i < STEPS.length - 1 && <div className={clsx('h-px w-4', i < idx ? 'bg-emerald-300' : 'bg-slate-200')} />}
          </div>
        )
      })}
      {stage === 'gap_plan' && active && <span className="ml-2 text-xs text-indigo-600">planning additional research…</span>}
    </div>
  )
}
