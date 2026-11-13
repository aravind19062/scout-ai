import { FileText, Search, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { Markdown } from '../components/Markdown'
import { Button, Card, CardHeader, EmptyState, ErrorState, Spinner } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import type { RagAnswer } from '../types/api'

export function Library() {
  const docs = useAsync(() => api.documents(), [])
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState<string>()
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [ans, setAns] = useState<RagAnswer>()

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    setErr(undefined)
    try {
      for (const f of Array.from(files)) await api.upload(f)
      await docs.reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function ask() {
    setAsking(true)
    setErr(undefined)
    try {
      setAns(await api.ragQuery(q))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setAsking(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Document library</h1>
          <p className="mt-1 text-sm text-slate-500">Upload your own documents to query them directly (RAG) or attach them to a research session.</p>
        </div>
        <input ref={fileRef} type="file" multiple accept=".pdf,.txt,.md,.html,.htm" className="hidden" onChange={(e) => upload(e.target.files)} />
        <Button loading={uploading} onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" /> Upload documents</Button>
      </div>
      {err && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{err}</div>}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Documents" subtitle="PDF, TXT, Markdown or HTML. Parsed in memory; only extracted text is stored." />
          {docs.loading ? <Spinner /> : docs.error ? <div className="p-5"><ErrorState error={docs.error} onRetry={docs.reload} /></div> : !docs.data?.length ? (
            <EmptyState title="No documents" body="Upload a PDF or text file to get started." icon={<FileText className="h-6 w-6" />} />
          ) : (
            <ul className="divide-y divide-slate-100">
              {docs.data.map((d) => (
                <li key={d.id} className="flex items-center gap-3 px-5 py-3">
                  <FileText className="h-4 w-4 text-slate-400" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800">{d.title}</p>
                    <p className="text-[11px] text-slate-500">{(d.char_count / 1000).toFixed(1)}k chars · {d.chunk_count} chunks · {d.extraction_method}</p>
                  </div>
                  <button onClick={async () => { await api.deleteDocument(d.id); void docs.reload() }} className="rounded p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-600" aria-label="Delete">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="self-start">
          <CardHeader title="Ask your documents" subtitle="Hybrid retrieval + grounded answer with citations" icon={<Search className="h-4 w-4" />} />
          <div className="space-y-4 p-5">
            <div className="flex gap-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && q.length >= 3 && ask()}
                placeholder="Ask a question…" className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none" />
              <Button onClick={ask} loading={asking} disabled={q.trim().length < 3 || !docs.data?.length}>Ask</Button>
            </div>
            {ans && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                {ans.insufficient_evidence && <p className="mb-1 text-xs font-medium text-amber-600">Insufficient evidence in your documents</p>}
                <Markdown>{ans.answer}</Markdown>
                {ans.citations.length > 0 && (
                  <ul className="mt-2 border-t border-slate-200 pt-2 text-xs text-slate-600">
                    {ans.citations.map((c) => <li key={c.label}><b className="text-indigo-700">[{c.label}]</b> {c.title}</li>)}
                  </ul>
                )}
                <p className="mt-2 text-[11px] text-slate-400">{ans.retrieved_chunks} chunks retrieved</p>
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
