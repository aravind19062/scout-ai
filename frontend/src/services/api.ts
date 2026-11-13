import type {
  AppConfig, Claim, Depth, DocumentItem, FollowUp, JobDetail, JobSummary, RagAnswer, Report, Source, UsageTotals,
} from '../types/api'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: init?.body instanceof FormData ? init?.headers : { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the SCOUT AI server. Is the backend running?')
  }
  if (res.status === 204) return undefined as T
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const err = body?.error
    const detail = err?.details?.map((d: { msg: string }) => d.msg).join('; ')
    throw new ApiError(res.status, err?.code ?? 'error', detail || err?.message || `Request failed (${res.status})`)
  }
  return body as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  config: () => request<AppConfig>('/config'),
  usage: () => request<UsageTotals>('/usage'),
  listResearch: () => request<JobSummary[]>('/research?limit=100'),
  getResearch: (id: string) => request<JobDetail>(`/research/${id}`),
  createResearch: (question: string, depth: Depth, document_ids: string[]) =>
    post<JobSummary>('/research', { question, depth, document_ids }),
  cancel: (id: string) => post(`/research/${id}/cancel`),
  resume: (id: string) => post(`/research/${id}/resume`),
  deepen: (id: string, instructions: string) => post<JobSummary>(`/research/${id}/deepen`, { instructions }),
  remove: (id: string) => request<void>(`/research/${id}`, { method: 'DELETE' }),
  sources: (id: string) => request<Source[]>(`/research/${id}/sources`),
  source: (id: string, sid: string) => request<Source>(`/research/${id}/sources/${sid}`),
  claims: (id: string) => request<Claim[]>(`/research/${id}/claims`),
  report: (id: string, version?: number) => request<Report>(`/research/${id}/report${version ? `?version=${version}` : ''}`),
  regenerate: (id: string, key: string) => post<Report>(`/research/${id}/report/sections/${key}/regenerate`),
  exportUrl: (id: string, format: 'md' | 'html' | 'json', version?: number) =>
    `/api/research/${id}/report/export?format=${format}${version ? `&version=${version}` : ''}`,
  followups: (id: string) => request<FollowUp[]>(`/research/${id}/followups`),
  askFollowup: (id: string, question: string) => post<FollowUp>(`/research/${id}/followups`, { question }),
  documents: () => request<DocumentItem[]>('/documents'),
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<DocumentItem>('/documents', { method: 'POST', body: fd })
  },
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: 'DELETE' }),
  ragQuery: (question: string) => post<RagAnswer>('/rag/query', { question }),
}
