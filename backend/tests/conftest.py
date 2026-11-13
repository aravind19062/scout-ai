import os

# Tests never call paid APIs by default: force explicit mock providers before app import.
os.environ.setdefault("APP_ENV", "test")
os.environ["LLM_PROVIDER"] = os.environ.get("TEST_LLM_PROVIDER", "mock")
os.environ["EMBEDDING_PROVIDER"] = os.environ.get("TEST_EMBEDDING_PROVIDER", "mock")
os.environ["SEARCH_PROVIDER"] = os.environ.get("TEST_SEARCH_PROVIDER", "mock")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://scout:scout@localhost:5433/scout_test"
)
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

TABLES = "research_events, followups, reports, claim_evidence, claims, findings, chunks, sources, research_tasks, research_jobs, llm_cache"


def _db_available() -> bool:
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(url, connect_timeout=2):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()


@pytest.fixture
async def db():
    """Clean database for an integration test. Skips if Postgres is not reachable."""
    if not DB_AVAILABLE:
        pytest.skip("PostgreSQL not available (start with: docker compose up -d db)")
    from app.database.session import dispose_engine, get_engine, init_engine

    await dispose_engine()
    init_engine(os.environ["DATABASE_URL"])
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield
    await dispose_engine()
