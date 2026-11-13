import clsx from 'clsx'
import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import type { Claim, Source } from '../types/api'
import { ClaimStatusBadge } from './badges'

const STANCE: Record<string, string> = {
  supports: 'border-emerald-300 bg-emerald-50/40',
  partial: 'border-lime-300 bg-lime-50/40',
  contradicts: 'border-rose-300 bg-rose-50/40',
}

export function ClaimList({ claims, sources, compact, onOpenSource }: { claims: Claim[]; sources: Source[]; compact?: boolean; onOpenSource?: (id: string) => void }) {
  const [open, setOpen] = useState<string>()
  const byId = new Map(sources.map((s) => [s.id, s]))
  if (!claims.length) return <p className="px-5 py-6 text-center text-sm text-slate-400">Claims appear here once analysis and verification run.</p>
  return (
    <ul className="divide-y divide-slate-100">
      {claims.map((c) => {
        const isOpen = open === c.id
        const ev = c.evidence.filter((e) => e.stance !== 'irrelevant')
        return (
          <li key={c.id} className="px-5 py-3">
            <button className="flex w-full items-start gap-3 text-left" onClick={() => setOpen(isOpen ? undefined : c.id)} disabled={compact}>
              <ClaimStatusBadge status={c.status} />
              <span className={clsx('flex-1 text-sm text-slate-700', compact && 'line-clamp-2')}>{c.statement}</span>
              {!compact && <ChevronDown className={clsx('mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition', isOpen && 'rotate-180')} />}
            </button>
            {isOpen && (
              <div className="mt-3 space-y-2 pl-1">
                {c.rationale && <p className="text-xs text-slate-500">{c.rationale}</p>}
                <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
                  <span>importance {c.importance}</span><span>{c.kind}</span><span>{c.category}</span>
                  <span>{c.support_count} supporting · {c.contradict_count} contradicting · {c.distinct_domains} independent source(s)</span>
                </div>
                {ev.length === 0 && <p className="text-xs italic text-slate-400">No relevant evidence was found.</p>}
                {ev.map((e) => {
                  const s = byId.get(e.source_id)
                  return (
                    <blockquote key={e.chunk_id} className={clsx('rounded-r-md border-l-4 px-3 py-2 text-xs', STANCE[e.stance])}>
                      <p className="text-slate-700">“{e.quote}”</p>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span className="font-semibold uppercase">{e.stance}</span>
                        {e.quote_verified && <span className="text-emerald-600">✓ quote found in source</span>}
                        {!e.numbers_consistent && <span className="text-amber-600">numbers differ from claim</span>}
                        {s && (
                          <button className="truncate text-indigo-600 hover:underline" onClick={() => onOpenSource?.(s.id)}>
                            {s.title} ({s.domain ?? s.source_type})
                          </button>
                        )}
                      </div>
                    </blockquote>
                  )
                })}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
