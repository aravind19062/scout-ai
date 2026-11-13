"""Guardrails for running SCOUT AI as a public demo on your own API keys (DEMO_MODE=true).

Every research job already has hard per-job limits (tokens, cost, tool calls, time). Demo mode adds
limits *across* jobs, so anonymous visitors cannot run up an unbounded bill:

  * depth is capped (DEMO_MAX_DEPTH),
  * each client may start DEMO_JOBS_PER_CLIENT_PER_DAY jobs (clients are identified by a salted hash
    of their IP; raw IPs are never stored),
  * a global daily spend cap: new paid work is refused once today's recorded spend plus the remaining
    budget *reserved* by queued/running jobs would exceed DEMO_DAILY_COST_CAP_USD. Reserving the
    worst case of in-flight jobs means the cap holds even when many jobs start at once,
  * uploads, "investigate further" and section regeneration can be disabled.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import Float, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import get_settings
from app.core.errors import ScoutError
from app.models import ResearchJob
from app.orchestration.graph import REPORT_RESERVE

DEPTH_ORDER = ("quick", "standard", "deep")


class DemoLimitReached(ScoutError):
    status_code = 429
    code = "demo_limit"


class DemoFeatureDisabled(ScoutError):
    status_code = 403
    code = "demo_disabled"


def client_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    # Salted with the app name + DB URL so the hash is not a portable IP fingerprint.
    s = get_settings()
    return hashlib.sha256(f"{s.app_name}|{s.database_url}|{ip}".encode()).hexdigest()[:32]


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def committed_spend_today(session: AsyncSession) -> tuple[float, float]:
    """(recorded spend of jobs touched today, budget still reserved by queued/running jobs)."""
    cost = func.coalesce(ResearchJob.usage["cost_usd"].as_string().cast(Float), 0.0)
    spent = await session.scalar(select(func.coalesce(func.sum(cost), 0.0)).where(ResearchJob.updated_at >= _today()))
    reserved = 0.0
    rows = await session.execute(select(ResearchJob.limits, ResearchJob.usage).where(ResearchJob.status.in_(["queued", "running"])))
    for limits, usage in rows:
        cap = float((limits or {}).get("max_cost_usd") or 0.0) * (1 + REPORT_RESERVE)
        reserved += max(0.0, cap - float((usage or {}).get("cost_usd", 0.0)))
    return float(spent or 0.0), reserved


async def check_budget(session: AsyncSession, extra_reserve_usd: float = 0.0) -> None:
    s = get_settings()
    if not s.demo_mode:
        return
    spent, reserved = await committed_spend_today(session)
    if spent + reserved + extra_reserve_usd > s.demo_daily_cost_cap_usd:
        raise DemoLimitReached("The public demo has reached today's spending limit. Please try again tomorrow "
                               "(or run SCOUT AI locally with your own API keys).")


async def check_new_job(session: AsyncSession, request: Request, depth: str, max_cost_usd: float) -> str | None:
    """Validate a new research job against demo limits. Returns the client key to store on the job."""
    s = get_settings()
    if not s.demo_mode:
        return None
    if DEPTH_ORDER.index(depth) > DEPTH_ORDER.index(s.demo_max_depth):
        raise DemoFeatureDisabled(f"The public demo allows research depth up to '{s.demo_max_depth}'.")
    key = client_key(request)
    used = await session.scalar(select(func.count()).select_from(ResearchJob)
                                .where(ResearchJob.client_key == key, ResearchJob.created_at >= _today()))
    if used >= s.demo_jobs_per_client_per_day:
        raise DemoLimitReached(f"The public demo allows {s.demo_jobs_per_client_per_day} research jobs per visitor per day.")
    await check_budget(session, extra_reserve_usd=max_cost_usd * (1 + REPORT_RESERVE))
    return key


def forbid_in_demo(feature: str, allowed: bool = False) -> None:
    if get_settings().demo_mode and not allowed:
        raise DemoFeatureDisabled(f"{feature} is disabled in the public demo.")


async def demo_status(session: AsyncSession, request: Request) -> dict:
    s = get_settings()
    if not s.demo_mode:
        return {"enabled": False}
    spent, reserved = await committed_spend_today(session)
    used = await session.scalar(select(func.count()).select_from(ResearchJob)
                                .where(ResearchJob.client_key == client_key(request), ResearchJob.created_at >= _today()))
    return {"enabled": True, "max_depth": s.demo_max_depth, "jobs_per_day": s.demo_jobs_per_client_per_day,
            "jobs_used_today": int(used or 0), "uploads": s.demo_allow_uploads,
            "budget_remaining_fraction": round(max(0.0, 1 - (spent + reserved) / s.demo_daily_cost_cap_usd), 3)}
