"""Live smoke test against real OpenAI + Tavily. Costs money; never runs by default.

    RUN_LIVE=1 TEST_LLM_PROVIDER=openai TEST_EMBEDDING_PROVIDER=openai TEST_SEARCH_PROVIDER=tavily \
    OPENAI_API_KEY=... TAVILY_API_KEY=... uv run pytest tests/live -m live -s
"""

import asyncio
import os

import pytest
from sqlalchemy import select

from app.database.session import session_scope
from app.models import Report, ResearchJob, Source

pytestmark = [pytest.mark.live, pytest.mark.integration,
              pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run paid live tests")]


async def test_live_quick_research(db):
    from app.orchestration.runner import job_manager
    from app.services.research import create_job

    job = await create_job("What are the temperature goals of the Paris Agreement and when was it adopted?", "quick")
    await asyncio.wait_for(job_manager._tasks[job.id], 600)
    async with session_scope() as s:
        j = await s.get(ResearchJob, job.id)
        rep = (await s.execute(select(Report).where(Report.job_id == job.id))).scalar_one()
        srcs = list((await s.execute(select(Source).where(Source.job_id == job.id))).scalars())
    assert j.status == "completed", j.error
    assert srcs and all(s.url.startswith("http") for s in srcs)
    assert rep.references and all(r["url"] in {s.url for s in srcs} for r in rep.references)
    print(j.usage)
