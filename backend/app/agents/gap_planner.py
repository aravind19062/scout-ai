"""Gap planning: decide what additional research is needed after verification."""

from __future__ import annotations

from sqlalchemy import select, update

from app.agents.planner import persist_tasks
from app.database.session import session_scope
from app.llm.prompts import PLANNER_SYSTEM
from app.models import Claim, ResearchJob, ResearchTask
from app.orchestration.context import JobContext
from app.schemas.llm_outputs import GapPlan

WEAK = ("unsupported", "unable_to_verify", "partially_supported")
MIN_BUDGET_FOR_NEW_ROUND = 0.3  # don't start a round we probably can't finish


async def weak_claims(ctx: JobContext) -> list[Claim]:
    async with session_scope() as s:
        return list((await s.execute(
            select(Claim).where(Claim.job_id == ctx.job_id, Claim.status.in_(WEAK), Claim.importance <= 2, Claim.verify_attempts < 2)
        )).scalars())


async def needs_more_research(ctx: JobContext, iteration: int, analysis: dict) -> tuple[bool, str]:
    if iteration + 1 >= ctx.depth.max_iterations:
        return False, f"maximum research rounds reached ({ctx.depth.max_iterations})"
    remaining = ctx.usage.remaining_fraction()
    if remaining < MIN_BUDGET_FOR_NEW_ROUND:
        return False, f"insufficient remaining budget ({remaining:.0%})"
    weak = await weak_claims(ctx)
    gaps = analysis.get("gaps", [])
    if not weak and not gaps:
        return False, "evidence is sufficient: no weak claims or gaps"
    return True, f"{len(weak)} weakly-supported claims and {len(gaps)} gaps"


async def plan_gap_research(ctx: JobContext, iteration: int, analysis: dict) -> int:
    weak = await weak_claims(ctx)
    async with session_scope() as s:
        existing = list((await s.execute(select(ResearchTask.objective).where(ResearchTask.job_id == ctx.job_id))).scalars())
    gaps = analysis.get("gaps", [])
    user = (
        f"Original research question:\n{ctx.question}\n\n"
        f"Existing research tasks (do not repeat):\n- " + "\n- ".join(existing) + "\n\n"
        "Evidence gaps:\n- " + ("\n- ".join(g["description"] for g in gaps) or "none") + "\n\n"
        "Claims that could not be adequately verified:\n- " + ("\n- ".join(c.statement for c in weak) or "none") + "\n\n"
        f"Create at most {ctx.depth.max_gap_tasks} NEW targeted tasks that would close these gaps or independently "
        "verify these claims (e.g. search for primary/official sources)."
    )
    plan = await ctx.llm.structured(
        task="gap_plan", system=PLANNER_SYSTEM, user=user, schema=GapPlan,
        inputs={"question": ctx.question, "gaps": gaps, "weak_claims": [c.statement for c in weak],
                "existing_objectives": existing, "max_tasks": ctx.depth.max_gap_tasks},
    )
    created = await persist_tasks(ctx, plan.tasks, origin="gap", iteration=iteration + 1, limit=ctx.depth.max_gap_tasks)
    async with session_scope() as s:
        if created and weak:
            # Re-verify weak claims once against the expanded evidence base.
            await s.execute(update(Claim).where(Claim.id.in_([c.id for c in weak])).values(status="pending"))
        job = await s.get(ResearchJob, ctx.job_id)
        job.iteration = iteration + 1
    return len(created)
