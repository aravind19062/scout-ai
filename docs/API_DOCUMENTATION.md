# API Documentation

Base URL: `http://localhost:8010` (direct) or `http://localhost:8080/api` (via the UI's nginx).
Interactive OpenAPI docs: `/docs`. Errors are `{"error": {"code", "message", "details?"}}`.
Mutating endpoints are rate limited per IP (`RATE_LIMIT_PER_MINUTE`, HTTP 429 + `Retry-After`).

## Research

| Method | Path | Description |
|---|---|---|
| POST | `/api/research` | Start a job. Body: `{question (10–2000 chars), depth: quick\|standard\|deep, document_ids?: uuid[]}` → 202 `JobSummary` |
| GET | `/api/research?limit&offset&status` | List jobs (newest first) |
| GET | `/api/research/{id}` | `JobDetail`: status, stage, limits, usage, analysis (themes, disagreements, gaps), tasks, counts |
| DELETE | `/api/research/{id}` | Delete job, its sources and reports (409 while running) |
| POST | `/api/research/{id}/cancel` | Request cancellation (409 if already finished) |
| POST | `/api/research/{id}/resume` | Resume a failed/cancelled job from its persisted stage |
| POST | `/api/research/{id}/deepen` | `{instructions}`: extra targeted research round → new report version |
| GET | `/api/research/{id}/events` | **SSE** stream. Event `progress` (`{id,type,stage,message,data,created_at}`), final event `end` (`{status}`). Supports `Last-Event-ID` / `?after=` replay |
| GET | `/api/research/{id}/tasks` | Research tasks |
| GET | `/api/research/{id}/sources` | Sources with metadata (URL, title, domain, published/retrieved dates, flags) |
| GET | `/api/research/{id}/sources/{source_id}` | Source including extracted text |
| GET | `/api/research/{id}/findings` | Findings with verified quotes |
| GET | `/api/research/{id}/claims` | Claims with status, rationale and evidence (stance, quote, checks) |
| GET | `/api/research/{id}/report?version=` | Report (latest by default) + `versions` |
| GET | `/api/research/{id}/report/export?format=md\|html\|json&version=` | Download |
| POST | `/api/research/{id}/report/sections/{key}/regenerate` | Regenerate a narrative section (`executive_summary, key_findings, topic_analysis, contradictions, opportunities, risks, outlook`) |
| GET/POST | `/api/research/{id}/followups` | List / ask (`{question}`) grounded follow-up questions (job must be completed) |

## Documents & RAG

| Method | Path | Description |
|---|---|---|
| POST | `/api/documents` | multipart `file` (.pdf .txt .md .html, ≤ `UPLOAD_MAX_MB`) → library document |
| GET | `/api/documents` | List library documents |
| DELETE | `/api/documents/{id}` | Delete |
| POST | `/api/rag/search` | `{question, k, mode, job_id?, document_ids?, source_types?, domains?}` → ranked chunks with vector similarity and keyword rank |
| POST | `/api/rag/query` | `{question, k, mode, job_id?, document_ids?}` → `{answer, insufficient_evidence, citations, retrieved_chunks}` |

## System

| GET | `/api/health` | DB connectivity + provider mode |
|---|---|---|
| GET | `/api/config` | Providers, mock flag, depth profiles (actual limits), upload limits, pricing |
| GET | `/api/usage` | Totals across jobs (tokens, estimated cost, tool calls, cache hits, by status) |

## Example

```bash
JOB=$(curl -s -XPOST localhost:8010/api/research -H 'content-type: application/json' \
  -d '{"question":"Analyse the Indian EV market: competitors, market size, policy and outlook.","depth":"standard"}' | jq -r .id)
curl -N localhost:8010/api/research/$JOB/events        # live progress
curl -s localhost:8010/api/research/$JOB/report | jq '.sections[0].markdown'
curl -OJ "localhost:8010/api/research/$JOB/report/export?format=md"
```
