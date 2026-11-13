from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.session import get_session
from app.models import ResearchJob
from app.orchestration.depth import DEPTHS

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "providers": get_settings().mode_summary}


@router.get("/config")
async def config():
    s = get_settings()
    return {
        "providers": s.mode_summary,
        "mock_mode": s.is_mock,
        "depths": {k: v.as_dict() for k, v in DEPTHS.items()},
        "upload": {"max_mb": s.upload_max_mb, "types": [".pdf", ".txt", ".md", ".html"]},
        "pricing_per_mtok": {"input": s.price_input_per_mtok, "output": s.price_output_per_mtok, "embedding": s.price_embedding_per_mtok},
    }


@router.get("/usage")
async def usage(session: AsyncSession = Depends(get_session)):
    jobs = list((await session.execute(select(ResearchJob.usage, ResearchJob.status))).all())
    tot = {"jobs": len(jobs), "total_tokens": 0, "cost_usd": 0.0, "tool_calls": 0, "llm_calls": 0, "llm_cache_hits": 0}
    by_status: dict[str, int] = {}
    for u, st in jobs:
        by_status[st] = by_status.get(st, 0) + 1
        for k in ("total_tokens", "tool_calls", "llm_calls", "llm_cache_hits"):
            tot[k] += int((u or {}).get(k, 0))
        tot["cost_usd"] += float((u or {}).get("cost_usd", 0.0))
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    tot["by_status"] = by_status
    tot["cost_is_estimate"] = True
    return tot
