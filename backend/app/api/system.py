from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import demo
from app.core.config import get_settings
from app.database.session import get_session
from app.models import Claim, ResearchJob
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
async def config(request: Request, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    return {
        "demo": await demo.demo_status(session, request),
        "job_execution": s.job_execution,
        "reranker": s.reranker,
        "providers": s.mode_summary,
        "mock_mode": s.is_mock,
        "depths": {k: v.as_dict() for k, v in DEPTHS.items()},
        "upload": {"max_mb": s.upload_max_mb, "types": [".pdf", ".txt", ".md", ".html"]},
        "pricing_per_mtok": {"input": s.price_input_per_mtok, "output": s.price_output_per_mtok, "embedding": s.price_embedding_per_mtok},
    }


@router.get("/usage")
async def usage(session: AsyncSession = Depends(get_session)):
    """Aggregate usage across all jobs: totals, per-agent-stage cost/time, and per-report averages."""
    jobs = list((await session.execute(select(ResearchJob.usage, ResearchJob.status))).all())
    tot = {"jobs": len(jobs), "total_tokens": 0, "cost_usd": 0.0, "tool_calls": 0, "llm_calls": 0, "llm_cache_hits": 0}
    by_status: dict[str, int] = {}
    by_stage: dict[str, dict[str, float]] = {}
    done_cost, done_time = [], []
    for u, st in jobs:
        u = u or {}
        by_status[st] = by_status.get(st, 0) + 1
        for k in ("total_tokens", "tool_calls", "llm_calls", "llm_cache_hits"):
            tot[k] += int(u.get(k, 0))
        tot["cost_usd"] += float(u.get("cost_usd", 0.0))
        for stage, b in (u.get("by_stage") or {}).items():
            agg = by_stage.setdefault(stage, {"cost_usd": 0.0, "elapsed_s": 0.0, "llm_calls": 0, "tool_calls": 0})
            for k in agg:
                agg[k] += b.get(k, 0)
        if st == "completed":
            done_cost.append(float(u.get("cost_usd", 0.0)))
            done_time.append(float(u.get("elapsed_s", 0.0)))
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    tot["by_status"] = by_status
    tot["by_stage"] = {k: {**v, "cost_usd": round(v["cost_usd"], 4), "elapsed_s": round(v["elapsed_s"], 1)} for k, v in by_stage.items()}
    tot["per_completed_job"] = {
        "avg_cost_usd": round(sum(done_cost) / len(done_cost), 4) if done_cost else None,
        "avg_elapsed_s": round(sum(done_time) / len(done_time), 1) if done_time else None,
    }
    calls = tot["llm_calls"] + tot["llm_cache_hits"]
    tot["llm_cache_hit_rate"] = round(tot["llm_cache_hits"] / calls, 4) if calls else None
    claims = dict((await session.execute(select(Claim.status, func.count()).group_by(Claim.status))).all())
    verified = sum(v for k, v in claims.items() if k != "pending")
    tot["claims"] = {"by_status": claims, "supported_rate": round(
        (claims.get("supported", 0) + claims.get("partially_supported", 0)) / verified, 4) if verified else None}
    tot["cost_is_estimate"] = True
    return tot
