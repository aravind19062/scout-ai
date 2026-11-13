"""Research worker: claims queued jobs from Postgres and runs them (JOB_EXECUTION=worker).

    uv run python -m app.worker

Queue semantics (Postgres is the broker; no Redis/Celery):
  * Claim: `UPDATE ... WHERE id = (SELECT ... WHERE status='queued' ORDER BY created_at
    FOR UPDATE SKIP LOCKED LIMIT 1)`. Any number of workers can poll concurrently; each job is
    claimed by exactly one of them, atomically with its status change.
  * Lease: a worker heartbeats its running jobs every WORKER_HEARTBEAT_S. A `running` job whose
    heartbeat is older than WORKER_LEASE_S belongs to a dead worker; any live worker requeues it
    (it resumes from its last completed stage, and cached LLM calls are not paid twice), or fails it
    after WORKER_MAX_ATTEMPTS claims so a poison job cannot crash-loop the fleet.
  * Cancel: the API only sets `cancel_requested`. Jobs check it between every tool/LLM call; the
    worker also notices it on heartbeat and hard-cancels after a grace period.
  * Shutdown (SIGTERM/SIGINT): stop claiming, interrupt running jobs, hand them back to the queue.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import uuid

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.tracing import configure_tracing
from app.database.session import dispose_engine, init_engine, session_scope
from app.orchestration.events import event_bus
from app.orchestration.runner import JobManager

log = get_logger(__name__)

CLAIM_SQL = text("""
UPDATE research_jobs
   SET status = 'running', worker_id = :worker, heartbeat_at = now(), run_attempts = run_attempts + 1
 WHERE id = (
     SELECT id FROM research_jobs
      WHERE status = 'queued' AND NOT cancel_requested
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1)
RETURNING id, run_attempts
""")

HEARTBEAT_SQL = text("""
UPDATE research_jobs SET heartbeat_at = now()
 WHERE worker_id = :worker AND status = 'running' AND id = ANY(:ids)
RETURNING id, cancel_requested
""")

# Orphans: running, but the owning worker stopped heartbeating. Requeue (or fail once attempts are used up).
REAP_SQL = text("""
UPDATE research_jobs
   SET status = CASE WHEN run_attempts >= :max_attempts THEN 'failed' ELSE 'queued' END,
       error = CASE WHEN run_attempts >= :max_attempts
                    THEN 'Worker lost ' || run_attempts || ' times; giving up. Use ''Resume'' to retry.'
                    ELSE NULL END,
       completed_at = CASE WHEN run_attempts >= :max_attempts THEN now() ELSE NULL END,
       worker_id = NULL
 WHERE status = 'running'
   AND heartbeat_at < now() - make_interval(secs => :lease)
RETURNING id, status
""")


class Worker:
    def __init__(self, worker_id: str | None = None, manager: JobManager | None = None):
        self.settings = get_settings()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.manager = manager or JobManager()
        self.capacity = self.settings.max_concurrent_jobs
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()  # set when a job finishes so a free slot is refilled immediately

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ---- queue operations (public so tests can drive them step by step) ------------------------
    async def claim(self) -> uuid.UUID | None:
        async with session_scope() as s:
            row = (await s.execute(CLAIM_SQL, {"worker": self.worker_id})).first()
        if row is None:
            return None
        log.info("worker.claimed", job_id=str(row.id), attempt=row.run_attempts, worker=self.worker_id)
        await event_bus.emit(row.id, "job_claimed", f"Picked up by worker (attempt {row.run_attempts})", None,
                             {"worker": self.worker_id, "attempt": row.run_attempts})
        return row.id

    async def heartbeat(self) -> None:
        ids = self.manager.running_ids
        if not ids:
            return
        async with session_scope() as s:
            rows = (await s.execute(HEARTBEAT_SQL, {"worker": self.worker_id, "ids": ids})).all()
        for r in rows:
            if r.cancel_requested:
                self.manager.hard_cancel_later(r.id)

    async def reap(self) -> list[tuple[uuid.UUID, str]]:
        async with session_scope() as s:
            rows = [(r.id, r.status) for r in await s.execute(
                REAP_SQL, {"max_attempts": self.settings.worker_max_attempts, "lease": self.settings.worker_lease_s})]
        for job_id, status in rows:
            log.warning("worker.reaped", job_id=str(job_id), new_status=status)
            msg = ("Previous worker stopped responding; job requeued and will resume from its last stage"
                   if status == "queued" else "Previous workers stopped responding too many times; job failed")
            await event_bus.emit(job_id, "job_requeued" if status == "queued" else "job_failed", msg, None)
        return rows

    async def fill(self) -> int:
        """Claim jobs until this worker is at capacity or the queue is empty."""
        started = 0
        while not self._stop.is_set() and len(self.manager.running_ids) < self.capacity:
            job_id = await self.claim()
            if job_id is None:
                break
            task = self.manager.run_now(job_id)
            task.add_done_callback(lambda _t: self._wake.set())
            started += 1
        return started

    # ---- main loop -------------------------------------------------------------------------------
    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.heartbeat()
                await self.reap()
            except Exception:
                log.exception("worker.heartbeat_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), self.settings.worker_heartbeat_s)
            except TimeoutError:
                pass

    async def run(self) -> None:
        log.info("worker.started", worker=self.worker_id, capacity=self.capacity)
        hb = asyncio.create_task(self._heartbeat_loop(), name="worker-heartbeat")
        try:
            while not self._stop.is_set():
                try:
                    await self.fill()
                except Exception:
                    log.exception("worker.claim_failed")
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self.settings.worker_poll_s)
                except TimeoutError:
                    pass
        finally:
            log.info("worker.stopping", worker=self.worker_id, running=len(self.manager.running_ids))
            self._stop.set()
            await hb
            await self.manager.shutdown()  # running jobs go back to the queue


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    configure_tracing(f"{s.otel_service_name}-worker")
    if s.job_execution != "worker":
        # An inline-mode API starts jobs itself; a worker claiming the same queued rows would double-run them.
        raise SystemExit("Refusing to start: set JOB_EXECUTION=worker for the API *and* the workers.")
    problems = s.provider_problems()
    if problems and s.app_env != "test":
        raise SystemExit("Invalid provider configuration: " + " | ".join(problems))
    init_engine()
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
