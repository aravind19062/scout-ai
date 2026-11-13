# Development Status

_Last updated: 2026-11-13_

**Current phase:** Phases 1–8 implemented. Next milestone: **first live run with real API keys** (see Pending).

| Phase | Status |
|---|---|
| 1. Architecture & foundation | ✅ Done |
| 2. LLM & RAG | ✅ Done |
| 3. Agent system | ✅ Done |
| 4. Autonomous research (loop, limits, persistence, cancellation, resume) | ✅ Done |
| 5. Frontend (dashboard, new research, live SSE view, report, verification, sources, follow-up, library) | ✅ Done |
| 6. Evaluation framework + mock-mode results | ✅ Done (live results pending keys) |
| 7. QA: tests, migrations, Docker, E2E | ✅ Done in mock mode |
| 8. Production readiness: worker queue, reranker, tracing, cost attribution, demo guardrails, CI, prod deploy | ✅ Done in mock mode |

## Implemented features

Planner / researcher / analyst / verifier / gap planner / reporter agents; LangGraph workflow with resume-by-stage;
token, cost, tool-call, time and round limits; cancellation; task retries; LLM response cache; SSRF-safe fetching; HTML/PDF
extraction; chunking; embedding reuse; pgvector HNSW + FTS hybrid retrieval with RRF; grounded QA; quote-verified
findings; rule-based verification; 11-section report with validated citations; follow-ups; deepen; section
regeneration; report versions; MD/HTML/JSON/print export; document library + standalone RAG; rate limiting;
prompt-injection defences; evaluation (retrieval, answer, citation, agent, system metrics; 3 baselines).
Phase 8: Postgres-backed worker queue (SKIP LOCKED claim, heartbeat leases, orphan requeue, max attempts, graceful
hand-back on SIGTERM); cross-encoder reranker (fastembed/ONNX) with timeout fallback and per-process inference
serialisation; OpenTelemetry traces (GenAI semconv) + Jaeger profile; per-agent-stage cost/time attribution in
API and UI; public-demo guardrails (depth cap, per-visitor quota on hashed IP, daily spend cap reserving in-flight
budget, per-visitor job list); GitHub Actions CI; production compose (Caddy HTTPS, N workers).

## Tests executed (actual results, 2026-11-13)

| Check | Result |
|---|---|
| `uv run pytest` (unit + integration on PostgreSQL 17 / pgvector, mock providers) | **92 passed, 1 skipped** (live test, needs `RUN_LIVE=1` + keys) |
| `uv run ruff check .` | clean |
| `alembic upgrade head → downgrade base → upgrade head` | clean |
| `npm run lint` (oxlint) / `npm run build` (tsc strict + vite) | clean / success |
| `docker compose build` | backend (with baked reranker model) and frontend images build |
| Worker crash test (real processes) | worker `SIGKILL`ed mid-job → requeued after lease (6 s) → claimed by a second worker (attempt 2) → resumed at the interrupted stage (`analyze`) → completed |
| Production compose on localhost (Caddy HTTPS, 2 workers, demo mode, cross-encoder) | deep depth refused (403), 3 quick jobs accepted, 4th refused (429); 3 concurrent jobs completed on 2 workers with 0 rerank fallbacks |
| UI | Dashboard (cost by agent, per-report averages), New research (demo limits), Live research, Report checked via headless Chrome screenshots |
| Retrieval eval with reranking (mock embeddings) | recorded in `docs/EVALUATION.md` (reranking: MRR 0.93 → 1.00 for hybrid; fixture set saturated) |

## Known issues

- **No live OpenAI/Tavily run yet** (no keys in the build environment). The OpenAI and Tavily adapters are unit-tested
  against mocked HTTP (structured output parsing, strict schema, retries, refusal, auth errors), but not against the
  real services.
- Agent runs are not bit-for-bit reproducible even in mock mode (concurrent tasks race for shared URLs); metrics are reported as ranges.
- Hybrid retrieval did not beat vector-only on the offline labelled set; the default (`hybrid`) is unvalidated with real embeddings.
- Mock-mode answer quality (fact recall ≈ 0.15) is low by construction and not indicative of real quality.
- `evaluation/results/` contains a few runs from before the retrieval tie-break/HNSW fixes (timestamps up to 20261106T0940); they can be deleted.

## Pending

1. Add `OPENAI_API_KEY` / `TAVILY_API_KEY`, run `RUN_LIVE=1 … pytest tests/live`, then the `real_world.json` evaluation; record results.
2. Compare `RETRIEVAL_MODE`s with real embeddings.
3. Compare `RERANKER`s on a live-web labelled retrieval set (the fixture set is saturated).
4. Deploy `docker-compose.prod.yml` to a VM with a domain; add the URL and CI badge to the README.
5. Optional: shared (Postgres/Redis) API rate limiter for multiple API replicas, OCR for scanned PDFs.
