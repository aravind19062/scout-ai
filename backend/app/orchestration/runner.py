"""Job manager: runs research jobs in the background, with cancellation, timeouts and resume.

Two execution modes (JOB_EXECUTION):
  * inline: `start()` runs the job as an asyncio task in this process (bounded by MAX_CONCURRENT_JOBS).
  * worker: `start()` only leaves the job queued in Postgres; `app.worker` processes claim and run it
    via `run_now()`. See docs/ARCHITECTURE.md ("Background execution").
Progress and evidence are persisted continuously, so an interrupted job can be inspected and resumed.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import ConflictError, JobCancelled, NotFoundError
from app.core.logging import get_logger
from app.core.tracing import set_attrs, span
from app.database.base import utcnow
from app.database.session import session_scope
from app.llm.factory import get_embedder, get_llm
from app.llm.usage import Limits, UsageTracker, bind_usage, unbind_usage
from app.models import ResearchJob, ResearchTask
from app.orchestration.context import JobContext
from app.orchestration.depth import from_dict
from app.orchestration.events import EventBus, event_bus
from app.orchestration.graph import build_graph, entry_for
from app.retrieval.fetcher import get_fetcher
from app.retrieval.search import get_search_provider

log = get_logger(__name__)
TERMINAL = ("completed", "failed", "cancelled")
HARD_TIMEOUT_GRACE_S = 420  # beyond max_duration: covers the report reserve + in-flight calls
CANCEL_GRACE_S = 5.0


def build_context(job: ResearchJob, events: EventBus = event_bus, limited: bool = True) -> JobContext:
    depth = from_dict(job.limits)
    usage = UsageTracker.restore(job.usage, depth.limits() if limited else Limits())
    return JobContext(
        job_id=job.id, question=job.question, depth=depth, usage=usage, llm=get_llm(), embedder=get_embedder(),
        search=get_search_provider(), fetcher=get_fetcher(), events=events,
    )


class JobManager:
    def __init__(self, events: EventBus = event_bus):
        self.events = events
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(get_settings().max_concurrent_jobs)
        self._shutting_down = False

    def is_running(self, job_id: uuid.UUID) -> bool:
        t = self._tasks.get(job_id)
        return t is not None and not t.done()

    @property
    def inline(self) -> bool:
        return get_settings().job_execution == "inline"

    def start(self, job_id: uuid.UUID) -> asyncio.Task | None:
        """Schedule a queued job. Inline mode runs it here; worker mode leaves it for a worker to claim."""
        if not self.inline:
            return None
        return self.run_now(job_id)

    @property
    def running_ids(self) -> list[uuid.UUID]:
        return [j for j, t in self._tasks.items() if not t.done()]

    def run_now(self, job_id: uuid.UUID) -> asyncio.Task:
        if self.is_running(job_id):
            raise ConflictError("Job is already running")
        task = asyncio.create_task(self._run(job_id), name=f"research-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return task

    async def _run(self, job_id: uuid.UUID) -> None:
        async with self._sem:
            async with session_scope() as s:
                job = await s.get(ResearchJob, job_id)
                if job is None:
                    return
                job.status, job.error, job.cancel_requested = "running", None, False
                job.started_at = job.started_at or utcnow()
                # Tasks interrupted mid-flight become pending again.
                await s.execute(update(ResearchTask).where(ResearchTask.job_id == job_id, ResearchTask.status == "running").values(status="pending"))
            ctx = build_context(job, self.events)
            token = bind_usage(ctx.usage)
            status, error = "completed", None
            try:
                await self.events.emit(job_id, "job_started", f"Research started ({ctx.depth.name})", job.stage,
                                       {"limits": job.limits, "providers": job.provider_mode})
                graph = build_graph(ctx)
                state = {"job_id": str(job_id), "entry": entry_for(job.stage), "iteration": job.iteration,
                         "analysis": job.analysis or {}, "halt_reason": job.halt_reason}
                with span("research.job", **{"scout.job_id": str(job_id), "scout.depth": ctx.depth.name,
                                             "scout.entry_stage": state["entry"], "scout.provider": ctx.llm.provider.name}) as sp:
                    async with asyncio.timeout(ctx.depth.max_duration_s + HARD_TIMEOUT_GRACE_S):
                        await graph.ainvoke(state, {"recursion_limit": 60})
                    set_attrs(sp, **{"scout.cost_usd": round(ctx.usage.cost_usd, 6), "scout.total_tokens": ctx.usage.total_tokens,
                                     "scout.tool_calls": ctx.usage.tool_calls})
            except (JobCancelled, asyncio.CancelledError):
                if self._shutting_down:
                    # Process shutdown, not a user cancel. Workers hand the job back to the queue (another
                    # worker resumes it from the last completed stage); inline mode marks it resumable.
                    status, error = (("failed", "Interrupted by a server shutdown. Use 'Resume' to continue.")
                                     if self.inline else ("queued", None))
                else:
                    status, error = "cancelled", "Cancelled by user"
            except TimeoutError:
                status, error = "failed", "Hard time limit exceeded"
            except Exception as e:
                log.exception("job.failed", job_id=str(job_id))
                status, error = "failed", f"{type(e).__name__}: {e}"[:2000]
            finally:
                unbind_usage(token)
                await self._finish(ctx, status, error)

    async def _finish(self, ctx: JobContext, status: str, error: str | None) -> None:
        async def write():
            async with session_scope() as s:
                job = await s.get(ResearchJob, ctx.job_id)
                if job is None:  # deleted out from under us (e.g. directly in the DB); nothing to record
                    log.warning("job.vanished", job_id=str(ctx.job_id))
                    return
                job.status, job.error, job.usage = status, error, ctx.usage.snapshot()
                job.completed_at = None if status == "queued" else utcnow()
                job.worker_id = None
            msg = {"completed": "Research completed", "cancelled": "Research cancelled", "failed": f"Research failed: {error}",
                   "queued": "Worker shutting down; job returned to the queue"}[status]
            await self.events.emit(ctx.job_id, f"job_{status}", msg, None, {"usage": ctx.usage.snapshot()})

        # Shield so the final state is recorded even when the task itself was cancelled.
        await asyncio.shield(write())

    async def cancel(self, job_id: uuid.UUID) -> None:
        async with session_scope() as s:
            job = await s.get(ResearchJob, job_id)
            if job is None:
                raise NotFoundError("Research job not found")
            if job.status in TERMINAL:
                raise ConflictError(f"Job is already {job.status}")
            job.cancel_requested = True
            if job.status == "queued" and not self.is_running(job_id):
                job.status, job.completed_at = "cancelled", utcnow()
        await self.events.emit(job_id, "cancel_requested", "Cancellation requested", None)
        self.hard_cancel_later(job_id)

    def hard_cancel_later(self, job_id: uuid.UUID) -> None:
        """Cancellation is cooperative first (checked between every tool/LLM call); if the job is running
        in this process, it is hard-cancelled after a grace period. Workers call this when they see the flag."""
        task = self._tasks.get(job_id)
        if task and not task.done():
            asyncio.get_running_loop().call_later(CANCEL_GRACE_S, lambda: task.done() or task.cancel())

    async def resume(self, job_id: uuid.UUID) -> None:
        async with session_scope() as s:
            job = await s.get(ResearchJob, job_id)
            if job is None:
                raise NotFoundError("Research job not found")
            if self.is_running(job_id) or job.status in ("running", "queued"):
                raise ConflictError(f"Job is already {job.status}")
            if job.status == "completed":
                raise ConflictError("Job already completed; use 'deepen' to extend it")
            job.status, job.cancel_requested, job.halt_reason, job.completed_at = "queued", False, None, None
        await self.events.emit(job_id, "job_resumed", f"Resuming from stage '{job.stage}'", job.stage)
        self.start(job_id)

    async def recover_orphans(self) -> int:
        """Called on API startup in inline mode: jobs left 'running' by a previous process are marked
        interrupted (resumable). In worker mode, workers own job state and requeue orphans themselves."""
        if not self.inline:
            return 0
        async with session_scope() as s:
            rows = list((await s.execute(select(ResearchJob).where(ResearchJob.status.in_(["running", "queued"])))).scalars())
            for j in rows:
                j.status, j.error = "failed", "Interrupted by a server restart. Use 'Resume' to continue from the last stage."
        return len(rows)

    async def shutdown(self) -> None:
        self._shutting_down = True
        for t in list(self._tasks.values()):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


job_manager = JobManager()
