# Development Status

_Last updated: 2026-11-06_

**Current phase:** Phases 1–7 implemented. Next milestone: **first live run with real API keys** (see Pending).

| Phase | Status |
|---|---|
| 1. Architecture & foundation | ✅ Done |
| 2. LLM & RAG | ✅ Done |
| 3. Agent system | ✅ Done |
| 4. Autonomous research (loop, limits, persistence, cancellation, resume) | ✅ Done |
| 5. Frontend (dashboard, new research, live SSE view, report, verification, sources, follow-up, library) | ✅ Done |
| 6. Evaluation framework + mock-mode results | ✅ Done (live results pending keys) |
| 7. QA: tests, migrations, Docker, E2E | ✅ Done in mock mode |

## Implemented features

Planner / researcher / analyst / verifier / gap planner / reporter agents; LangGraph workflow with resume-by-stage;
token, cost, tool-call, time and round limits; cancellation; task retries; LLM response cache; SSRF-safe fetching; HTML/PDF
extraction; chunking; embedding reuse; pgvector HNSW + FTS hybrid retrieval with RRF; grounded QA; quote-verified
findings; rule-based verification; 11-section report with validated citations; follow-ups; deepen; section
regeneration; report versions; MD/HTML/JSON/print export; document library + standalone RAG; rate limiting;
prompt-injection defences; evaluation (retrieval, answer, citation, agent, system metrics; 3 baselines).

## Tests executed (actual results)

| Check | Result |
|---|---|
| `uv run pytest` (unit + integration on PostgreSQL 17 / pgvector 0.8.7, mock providers) | **77 passed, 1 skipped** (live test, needs `RUN_LIVE=1` + keys) |
| `uv run ruff check app tests` | clean |
| `npm run build` (tsc strict + vite) | success |
| `npx oxlint src` | 1 cosmetic warning (react-refresh export rule in `badges.tsx`) |
| `docker compose up -d --build` | db, backend (migrations applied on start), frontend (nginx) healthy |
| E2E via nginx :8080 | deep job created → cancelled mid-research → resumed → completed (7 sources, 35 findings, 6 supported claims); SSE streamed through nginx |
| UI | Dashboard, New research, Live, Report, Verification screens rendered and checked (headless Chrome screenshots) |
| Evaluation (mock) | Retrieval and e2e runs recorded in `docs/EVALUATION.md` |

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
3. Optional: auth, multi-replica job queue (Redis), OCR for scanned PDFs.
