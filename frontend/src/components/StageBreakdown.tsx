import type { StageUsage } from '../types/api'
import { fmtDuration, fmtInt, fmtUsd } from '../utils'

/** Pipeline order; unknown stages (e.g. "other") are appended. */
const ORDER = ['plan', 'research', 'analyze', 'verify', 'gap_plan', 'report', 'followup']
const LABEL: Record<string, string> = {
  plan: 'Planner',
  research: 'Researcher',
  analyze: 'Analyst',
  verify: 'Verifier',
  gap_plan: 'Gap planner',
  report: 'Reporter',
  followup: 'Follow-ups',
  other: 'Other',
}

type Row = Pick<StageUsage, 'cost_usd' | 'elapsed_s' | 'llm_calls' | 'tool_calls'>

/**
 * Where a job's (or all jobs') money and time went, per agent. Each row shows the cost share as a
 * bar (absolute value printed beside it) and the time share as a thinner bar, so the two can be
 * compared: e.g. the researcher is slow (I/O) but cheap, the verifier the reverse.
 */
export function StageBreakdown({ stages }: { stages: Record<string, Row> }) {
  const keys = Object.keys(stages).sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99))
  const totalCost = keys.reduce((a, k) => a + stages[k].cost_usd, 0)
  const totalTime = keys.reduce((a, k) => a + stages[k].elapsed_s, 0)
  if (!keys.length) return <p className="px-5 py-4 text-xs text-slate-400">No usage recorded yet.</p>
  return (
    <div className="space-y-3 px-5 py-4">
      <div className="flex items-center gap-4 text-[11px] text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-indigo-500" /> cost</span>
        <span className="flex items-center gap-1.5"><span className="h-1 w-3 rounded-sm bg-slate-400" /> time</span>
      </div>
      <ul className="space-y-2.5">
        {keys.map((k) => {
          const r = stages[k]
          const cost = totalCost ? r.cost_usd / totalCost : 0
          const time = totalTime ? r.elapsed_s / totalTime : 0
          return (
            <li key={k} className="grid grid-cols-[6.5rem_1fr_auto] items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-medium text-slate-700">{LABEL[k] ?? k}</span>
              <div className="space-y-1" aria-hidden>
                <div className="h-2 rounded-sm bg-indigo-500" style={{ width: `${Math.max(cost * 100, cost ? 1 : 0)}%` }} />
                <div className="h-1 rounded-sm bg-slate-400" style={{ width: `${Math.max(time * 100, time ? 1 : 0)}%` }} />
              </div>
              <span className="text-right tabular-nums text-slate-600">
                {fmtUsd(r.cost_usd)} <span className="text-slate-400">· {fmtDuration(r.elapsed_s)}</span>
              </span>
              <span />
              <span className="col-span-2 text-[11px] text-slate-400">
                {fmtInt(r.llm_calls)} LLM calls · {fmtInt(r.tool_calls)} tool calls · {Math.round(cost * 100)}% of cost
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
