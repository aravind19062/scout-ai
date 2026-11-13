"""Job manager: runs research jobs in the background, with cancellation, timeouts and resume.

Jobs run as asyncio tasks inside the API process (bounded by MAX_CONCURRENT_JOBS). Progress and
evidence are persisted continuously, so a crashed/interrupted job can be inspected and resumed.
Why no Redis/Celery: see docs/ARCHITECTURE.md ("Background execution").
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import ConflictError, JobCancelled, NotFoundError
from app.core.logging import get_logger
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

    def is_running(self, job_id: uuid.UUID) -> bool:
        t = self._tasks.get(job_id)
        return t is not None and not t.done()

    def start(self, job_id: uuid.UUID) -> asyncio.Task:
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
                async with asyncio.timeout(ctx.depth.max_duration_s + HARD_TIMEOUT_GRACE_S):
                    await graph.ainvoke(state, {"recursion_limit": 60})
            except (JobCancelled, asyncio.CancelledError):
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
                job.status, job.error, job.usage = status, error, ctx.usage.snapshot()
                job.completed_at = utcnow()
            msg = {"completed": "Research completed", "cancelled": "Research cancelled", "failed": f"Research failed: {error}"}[status]
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
        task = self._tasks.get(job_id)
        if task and not task.done():
            # Cooperative first (checked between every tool/LLM call); hard-cancel after a grace period.
            asyncio.get_running_loop().call_later(CANCEL_GRACE_S, lambda: task.done() or task.cancel())

    async def resume(self, job_id: uuid.UUID) -> None:
        async with session_scope() as s:
            job = await s.get(ResearchJob, job_id)
            if job is None:
                raise NotFoundError("Research job not found")
            if self.is_running(job_id):
                raise ConflictError("Job is already running")
            if job.status == "completed":
                raise ConflictError("Job already completed; use 'deepen' to extend it")
            job.status, job.cancel_requested, job.halt_reason, job.completed_at = "queued", False, None, None
        await self.events.emit(job_id, "job_resumed", f"Resuming from stage '{job.stage}'", job.stage)
        self.start(job_id)

    async def recover_orphans(self) -> int:
        """Called on startup: jobs left 'running' by a previous process are marked interrupted (resumable)."""
        async with session_scope() as s:
            rows = list((await s.execute(select(ResearchJob).where(ResearchJob.status.in_(["running", "queued"])))).scalars())
            for j in rows:
                j.status, j.error = "failed", "Interrupted by a server restart. Use 'Resume' to continue from the last stage."
        return len(rows)

    async def shutdown(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


job_manager = JobManager()
