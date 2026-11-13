export type Depth = 'quick' | 'standard' | 'deep'
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ClaimStatus = 'pending' | 'supported' | 'partially_supported' | 'conflicting' | 'unsupported' | 'unable_to_verify'

export interface Usage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  embedding_tokens?: number
  llm_calls?: number
  llm_cache_hits?: number
  tool_calls?: number
  tool_failures?: number
  search_calls?: number
  fetch_calls?: number
  cost_usd?: number
  elapsed_s?: number
  by_stage?: Record<string, StageUsage>
}

/** Usage attributed to one agent stage (plan, research, analyze, verify, gap_plan, report, followup). */
export interface StageUsage {
  llm_calls: number
  llm_cache_hits?: number
  prompt_tokens?: number
  completion_tokens?: number
  embedding_tokens?: number
  tool_calls: number
  cost_usd: number
  elapsed_s: number
}

export interface JobSummary {
  id: string
  question: string
  depth: Depth
  status: JobStatus
  stage: string
  iteration: number
  halt_reason: string | null
  error: string | null
  usage: Usage
  provider_mode: Record<string, string>
  created_at: string
  completed_at: string | null
}

export interface Task {
  id: string
  key: string
  objective: string
  queries: string[]
  expected_evidence: string
  priority: number
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  origin: 'plan' | 'gap' | 'followup'
  iteration: number
  attempts: number
  summary: string | null
  error: string | null
}

export interface Analysis {
  themes?: { title: string; summary: string }[]
  disagreements?: { topic: string; positions: { position: string; source_ids: string[] }[] }[]
  gaps?: { description: string; suggested_queries: string[] }[]
}

export interface JobDetail extends JobSummary {
  limits: Record<string, number | string>
  analysis: Analysis
  started_at: string | null
  tasks: Task[]
  counts: { sources: number; findings: number; claims: Partial<Record<ClaimStatus, number>>; reports: number }
}

export interface Source {
  id: string
  url: string | null
  title: string
  domain: string | null
  source_type: 'web' | 'pdf' | 'upload'
  published_at: string | null
  retrieved_at: string
  char_count: number
  extraction_method: string
  search_query: string | null
  security_flags: string[]
  task_id: string | null
  text?: string
}

export interface Evidence {
  source_id: string
  chunk_id: string
  stance: 'supports' | 'partial' | 'contradicts' | 'irrelevant'
  quote: string
  quote_verified: boolean
  numbers_consistent: boolean
  relevance: number
}

export interface Claim {
  id: string
  statement: string
  category: string
  importance: number
  kind: string
  status: ClaimStatus
  rationale: string | null
  support_count: number
  contradict_count: number
  distinct_domains: number
  iteration: number
  evidence: Evidence[]
}

export interface ReportSection {
  key: string
  title: string
  markdown: string
  generated_by: 'llm' | 'system'
}

export interface Reference {
  label: string
  source_id: string
  title: string
  url: string | null
  domain: string | null
  published_at: string | null
  retrieved_at: string
  source_type: string
  security_flags: string[]
}

export interface Report {
  id: string
  job_id: string
  version: number
  title: string
  sections: ReportSection[]
  references: Reference[]
  citation_stats: {
    citations_in_text?: number
    unique_sources_cited?: number
    sources_available?: number
    invalid_citations_removed?: number
    claims_by_status?: Partial<Record<ClaimStatus, number>>
  }
  created_at: string
  versions: number[]
}

export interface Citation {
  label: string
  source_id: string
  title: string
  url: string | null
  quote_context: string
}

export interface FollowUp {
  id: string
  question: string
  answer: string
  citations: Citation[]
  insufficient_evidence: boolean
  created_at: string
}

export interface ProgressEvent {
  id: number
  type: string
  stage: string | null
  message: string
  data: Record<string, unknown>
  created_at: string
}

export interface DocumentItem {
  id: string
  title: string
  source_type: string
  char_count: number
  extraction_method: string
  retrieved_at: string
  security_flags: string[]
  chunk_count: number
}

export interface AppConfig {
  providers: Record<string, string>
  mock_mode: boolean
  depths: Record<Depth, Record<string, number | string>>
  upload: { max_mb: number; types: string[] }
  job_execution: 'inline' | 'worker'
  reranker: 'none' | 'cross_encoder' | 'lexical'
  demo: DemoStatus
}

export type DemoStatus =
  | { enabled: false }
  | { enabled: true; max_depth: Depth; jobs_per_day: number; jobs_used_today: number; uploads: boolean; budget_remaining_fraction: number }

export interface UsageTotals {
  jobs: number
  total_tokens: number
  cost_usd: number
  tool_calls: number
  llm_calls: number
  llm_cache_hits: number
  by_status: Partial<Record<JobStatus, number>>
  by_stage: Record<string, Pick<StageUsage, 'cost_usd' | 'elapsed_s' | 'llm_calls' | 'tool_calls'>>
  per_completed_job: { avg_cost_usd: number | null; avg_elapsed_s: number | null }
  llm_cache_hit_rate: number | null
  claims: { by_status: Partial<Record<ClaimStatus, number>>; supported_rate: number | null }
}

export interface RagAnswer {
  answer: string
  insufficient_evidence: boolean
  citations: Citation[]
  retrieved_chunks: number
}
