import { ExternalLink, FileText, Globe, ShieldAlert } from 'lucide-react'
import type { Source } from '../types/api'

export function SourceList({ sources, onOpen }: { sources: Source[]; onOpen: (id: string) => void }) {
  if (!sources.length) return <p className="px-5 py-6 text-center text-sm text-slate-400">Sources will appear here as they are retrieved.</p>
  return (
    <ul className="divide-y divide-slate-100">
      {sources.map((s) => (
        <li key={s.id} className="flex items-start gap-3 px-5 py-3">
          {s.source_type === 'web' ? <Globe className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" /> : <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />}
          <div className="min-w-0 flex-1">
            <button onClick={() => onOpen(s.id)} className="text-left text-sm font-medium text-slate-800 hover:text-indigo-700">{s.title}</button>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
              <span>{s.domain ?? s.source_type}</span>
              <span>published {s.published_at ?? 'unknown'}</span>
              <span>retrieved {new Date(s.retrieved_at).toLocaleString()}</span>
              <span>{(s.char_count / 1000).toFixed(1)}k chars · {s.extraction_method}</span>
            </div>
            {s.security_flags.length > 0 && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-amber-600"><ShieldAlert className="h-3 w-3" /> Possible prompt injection ({s.security_flags.join(', ')}); treated as untrusted data</p>
            )}
          </div>
          {s.url && (
            <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-indigo-600" aria-label="Open original">
              <ExternalLink className="h-4 w-4" />
            </a>
          )}
        </li>
      ))}
    </ul>
  )
}
