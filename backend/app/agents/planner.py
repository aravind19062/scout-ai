"""Research planning agent: question -> structured, de-duplicated task list."""

from __future__ import annotations

from sqlalchemy import func, select

from app.agents.common import clean_queries, dedupe_tasks
from app.database.session import session_scope
from app.llm.prompts import PLANNER_SYSTEM
from app.models import ResearchTask
from app.orchestration.context import JobContext
from app.schemas.llm_outputs import PlannedTask, ResearchPlan


async def plan_research(ctx: JobContext) -> list[ResearchTask]:
    async with session_scope() as s:
        existing = list(
            (await s.execute(select(ResearchTask).where(ResearchTask.job_id == ctx.job_id, ResearchTask.origin == "plan"))).scalars()
        )
    if existing:  # idempotent: resuming a job never re-plans (or re-pays for) an existing plan
        await ctx.emit("plan_reused", f"Reusing existing plan with {len(existing)} tasks", "plan")
        return existing

    d = ctx.depth
    user = (
        f"Research question:\n{ctx.question}\n\n"
        f"Create at most {d.max_tasks} tasks with up to {d.queries_per_task} search queries each. "
        "Order by importance. Avoid overlapping tasks."
    )
    plan = await ctx.llm.structured(
        task="plan", system=PLANNER_SYSTEM, user=user, schema=ResearchPlan,
        inputs={"question": ctx.question, "max_tasks": d.max_tasks},
    )
    return await persist_tasks(ctx, plan.tasks, origin="plan", iteration=0, limit=d.max_tasks, extra={"scope": plan.scope_notes})


async def persist_tasks(
    ctx: JobContext, planned: list[PlannedTask], origin: str, iteration: int, limit: int, extra: dict | None = None
) -> list[ResearchTask]:
    async with session_scope() as s:
        existing_objs = list((await s.execute(select(ResearchTask.objective).where(ResearchTask.job_id == ctx.job_id))).scalars())
        n_existing = await s.scalar(select(func.count()).select_from(ResearchTask).where(ResearchTask.job_id == ctx.job_id))
    unique = await dedupe_tasks(planned, existing_objs, ctx.embedder)
    dropped = len(planned) - len(unique)
    unique = sorted(unique, key=lambda t: min(max(t.priority, 1), 3))[:limit]
    created: list[ResearchTask] = []
    async with session_scope() as s:
        for i, t in enumerate(unique, start=(n_existing or 0) + 1):
            task = ResearchTask(
                job_id=ctx.job_id, key=f"T{i}", objective=t.objective.strip()[:1000],
                queries=clean_queries(t, ctx.depth.queries_per_task), expected_evidence=t.expected_evidence[:1000],
                priority=min(max(t.priority, 1), 3), origin=origin, iteration=iteration,
            )
            s.add(task)
            created.append(task)
    await ctx.emit(
        "plan_created" if origin == "plan" else "tasks_added",
        f"{len(created)} research tasks created" + (f" ({dropped} redundant tasks removed)" if dropped else ""),
        "plan" if origin == "plan" else "gap_plan",
        tasks=[{"key": t.key, "objective": t.objective, "queries": t.queries, "priority": t.priority, "origin": t.origin} for t in created],
        **(extra or {}),
    )
    return created
