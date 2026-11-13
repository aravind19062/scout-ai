# Development Guide

## Setup

```bash
docker compose up -d db                    # Postgres 17 + pgvector on :5433, creates `scout` and `scout_test`
cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --port 8010 --reload
cd ../frontend && nvm use && npm install && npm run dev   # :5173, proxies /api → :8010
```

Port note: the backend uses **8010** on the host (8000 is commonly taken); the UI container uses 8080.

## Offline mode

Set `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock SEARCH_PROVIDER=mock`. Search and fetch then serve
`backend/app/retrieval/fixtures/corpus.json`, a fictional corpus with deliberate edge cases: conflicting market-size
figures, an unreachable page, a prompt-injection page, a duplicate page and an off-topic page. Try the question
*"Analyse the Freedonia electric vehicle market…"* (one click in the New Research screen).

## Tests

```bash
cd backend
uv run pytest                # all (integration tests skip automatically if Postgres is down)
uv run pytest tests/unit     # fast, no DB
uv run pytest -k workflow -s # end-to-end workflow tests
uv run ruff check app tests
cd ../frontend && npm run build && npx oxlint src
```

Test layout: `tests/unit` (pure logic, providers via `httpx.MockTransport`), `tests/integration` (Postgres:
RAG, workflow, API, SSE), `tests/live` (paid; `RUN_LIVE=1`). Failure injection: `MockProvider(fail_tasks={...})`.

## Migrations

Edit models → `uv run alembic revision --autogenerate -m "..."` → review (pgvector types need `import pgvector`) →
`uv run alembic upgrade head`. Apply to the test DB with
`DATABASE_URL=postgresql+psycopg://scout:scout@localhost:5433/scout_test uv run alembic upgrade head`.
To change embedding dimensions, set `EMBEDDING_DIM` before creating the database (or write a migration that re-embeds).

## Extending

- **New search provider:** implement `SearchProvider.search()` in `retrieval/search.py` and select it in `get_search_provider()`.
- **New LLM provider:** implement `LLMProvider.structured()` (`llm/base.py`) and register it in `llm/factory.py`.
  The gateway gives you budgets, caching and accounting for free.
- **New agent step:** add a node in `orchestration/graph.py` via `_run_node`, add its stage to `STAGES` (for resume),
  keep it idempotent and write results to the DB.
- **New report section:** add it to `LLM_SECTIONS` (model-written) or `SECTION_ORDER` + a generator (data-written) in `agents/reporter.py`.

## Conventions

- Evidence goes to the DB, not graph state. Nodes must be safe to re-run.
- Every LLM call goes through `LLMGateway.structured()` with a `task` name and structured `inputs`.
- Retrieved text only enters prompts through `prompts.passage()`.
- Never claim support without a verbatim quote that code has checked.
