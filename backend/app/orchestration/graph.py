"""LangGraph research workflow.

    START ─(resume router)─► plan ─► research ─► analyze ─► verify ─┬─► report ─► END
                                       ▲                            │
                                       └──────── gap_plan ◄─────────┘  (bounded by max_iterations + budget)

Design choices (see docs/AGENT_WORKFLOW.md):
* Graph state is *control* state only (stage, iteration, halt reason, latest analysis summary).
  Evidence lives in Postgres; nodes are idempotent and read/write the DB, so the graph can be
  resumed from the persisted `job.stage` after a crash, cancellation or budget stop.
* Every node passes through `_run_node`, which records the stage, checks cancellation, enforces
  budgets, and converts a budget stop into a graceful jump to `report` (`halt_reason`).
* The loop is bounded three ways: max_iterations, remaining budget, and "no new tasks created".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.analyst import analyze
from app.agents.gap_planner import needs_more_research, plan_gap_research
from app.agents.planner import plan_research
from app.agents.reporter import generate_report
from app.agents.researcher import run_pending_tasks
from app.agents.verifier import verify_pending
from app.core.errors import BudgetExceeded
from app.database.session import session_scope
from app.llm.usage import Limits
from app.models import ResearchJob
from app.orchestration.context import JobContext

STAGES = ("plan", "research", "analyze", "verify", "gap_plan", "report")
REPORT_RESERVE = 0.25  # extra budget share reserved so a report can still be written after a budget stop


class ResearchState(TypedDict, total=False):
    job_id: str
    entry: str
    iteration: int
    halt_reason: str | None
    analysis: dict
    continue_research: bool
    new_tasks: int


async def _persist(ctx: JobContext, **fields) -> None:
    async with session_scope() as s:
        job = await s.get(ResearchJob, ctx.job_id)
        job.usage = ctx.usage.snapshot()
        for k, v in fields.items():
            setattr(job, k, v)


def build_graph(ctx: JobContext):
    def _run_node(stage: str, fn: Callable[[ResearchState], Awaitable[dict]]):
        async def node(state: ResearchState) -> dict:
            if state.get("halt_reason") and stage != "report":
                return {}
            await _persist(ctx, stage=stage)
            await ctx.emit("stage", f"Stage: {stage.replace('_', ' ')}", stage)
            try:
                await ctx.check_cancelled()
                if stage != "report":
                    ctx.usage.check()
                updates = await fn(state) or {}
            except BudgetExceeded as e:
                await ctx.emit("halted", f"Execution limit reached ({e.limit}); writing report with evidence gathered so far", stage, limit=e.limit)
                await _persist(ctx, halt_reason=e.limit)
                updates = {"halt_reason": e.limit}
            await _persist(ctx)
            return updates

        return node

    async def plan(state):
        tasks = await plan_research(ctx)
        return {"new_tasks": len(tasks)}

    async def research(state):
        await run_pending_tasks(ctx)
        return {}

    async def analyze_node(state):
        return {"analysis": await analyze(ctx, state.get("iteration", 0))}

    async def verify(state):
        counts = await verify_pending(ctx)
        await ctx.emit("verification_done", "Verification: " + (", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) or "no claims"), "verify", counts=counts)
        go, why = await needs_more_research(ctx, state.get("iteration", 0), state.get("analysis") or {})
        await ctx.emit("decision", ("Additional research needed: " if go else "Proceeding to report: ") + why, "verify", continue_research=go)
        return {"continue_research": go}

    async def gap_plan(state):
        n = await plan_gap_research(ctx, state.get("iteration", 0), state.get("analysis") or {})
        return {"new_tasks": n, "iteration": state.get("iteration", 0) + 1}

    async def report(state):
        lim = ctx.usage.limits
        ctx.usage.limits = Limits(
            max_tokens=int(lim.max_tokens * (1 + REPORT_RESERVE)) if lim.max_tokens else None,
            max_cost_usd=lim.max_cost_usd * (1 + REPORT_RESERVE) if lim.max_cost_usd else None,
            max_tool_calls=None,
            max_duration_s=(lim.max_duration_s + 300) if lim.max_duration_s else None,
        )
        try:
            await generate_report(ctx)
        finally:
            ctx.usage.limits = lim
        await _persist(ctx, stage="done")
        return {}

    g = StateGraph(ResearchState)
    for name, fn in (("plan", plan), ("research", research), ("analyze", analyze_node), ("verify", verify),
                     ("gap_plan", gap_plan), ("report", report)):
        g.add_node(name, _run_node(name, fn))

    def halted_or(nxt: str):
        return lambda s: "report" if s.get("halt_reason") else nxt

    g.add_conditional_edges(START, lambda s: s.get("entry", "plan"), {k: k for k in STAGES})
    g.add_conditional_edges("plan", lambda s: "report" if s.get("halt_reason") or not s.get("new_tasks") else "research", ["research", "report"])
    g.add_conditional_edges("research", halted_or("analyze"), ["analyze", "report"])
    g.add_conditional_edges("analyze", halted_or("verify"), ["verify", "report"])
    g.add_conditional_edges("verify", lambda s: "gap_plan" if s.get("continue_research") and not s.get("halt_reason") else "report", ["gap_plan", "report"])
    g.add_conditional_edges("gap_plan", lambda s: "research" if s.get("new_tasks") and not s.get("halt_reason") else "report", ["research", "report"])
    g.add_edge("report", END)
    return g.compile()


def entry_for(stage: str) -> str:
    return stage if stage in STAGES else "plan"


__all__ = ["build_graph", "entry_for"]
