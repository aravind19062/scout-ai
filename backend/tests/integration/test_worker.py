"""Durable worker queue (JOB_EXECUTION=worker): claiming, leases, requeue, shutdown, cancellation."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.database.base import utcnow
from app.database.session import session_scope
from app.models import ResearchEvent, ResearchJob
from app.orchestration.runner import JobManager, job_manager
from app.services import research as svc
from app.worker import Worker

pytestmark = pytest.mark.integration

Q = "Analyse the Freedonia electric vehicle market: market size, competitors, policy and outlook."


@pytest.fixture
def worker_mode(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "job_execution", "worker")
    return s


async def _job(job_id) -> ResearchJob:
    async with session_scope() as s:
        return await s.get(ResearchJob, job_id)


async def _drain(manager: JobManager, timeout=60):
    tasks = list(manager._tasks.values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout)


async def test_api_process_only_enqueues_and_worker_completes(db, worker_mode):
    job = await svc.create_job(Q, "quick")
    assert not job_manager.is_running(job.id)
    assert (await _job(job.id)).status == "queued"

    w = Worker("w1", JobManager())
    assert await w.fill() == 1
    await _drain(w.manager)

    job = await _job(job.id)
    assert job.status == "completed", job.error
    assert job.run_attempts == 1 and job.worker_id is None
    async with session_scope() as s:
        types = [e.type for e in (await s.execute(select(ResearchEvent).where(ResearchEvent.job_id == job.id))).scalars()]
    assert "job_claimed" in types and "job_completed" in types


async def test_concurrent_workers_never_claim_the_same_job(db, worker_mode):
    ids = {(await svc.create_job(Q, "quick")).id for _ in range(5)}
    workers = [Worker(f"w{i}", JobManager()) for i in range(4)]
    claims = await asyncio.gather(*(w.claim() for w in workers for _ in range(3)))
    claimed = [c for c in claims if c is not None]
    assert sorted(claimed) == sorted(ids)  # every job exactly once, extra claims get nothing
    async with session_scope() as s:
        rows = list((await s.execute(select(ResearchJob).where(ResearchJob.id.in_(ids)))).scalars())
    assert all(r.status == "running" and r.run_attempts == 1 and r.worker_id for r in rows)


async def test_cancelled_queued_job_is_never_claimed(db, worker_mode):
    job = await svc.create_job(Q, "quick")
    await job_manager.cancel(job.id)
    assert (await _job(job.id)).status == "cancelled"
    assert await Worker("w1", JobManager()).claim() is None


async def test_reaper_requeues_orphans_then_gives_up(db, worker_mode, monkeypatch):
    monkeypatch.setattr(worker_mode, "worker_max_attempts", 2)
    job = await svc.create_job(Q, "quick")
    dead, alive = Worker("dead", JobManager()), Worker("alive", JobManager())

    async def claim_and_die(w):
        assert await w.claim() == job.id
        async with session_scope() as s:  # the worker stops heartbeating
            await s.execute(update(ResearchJob).where(ResearchJob.id == job.id).values(heartbeat_at=utcnow() - timedelta(minutes=10)))

    await claim_and_die(dead)
    assert await alive.reap() == [(job.id, "queued")]
    j = await _job(job.id)
    assert j.status == "queued" and j.worker_id is None and j.error is None

    await claim_and_die(dead)  # second attempt also lost
    assert await alive.reap() == [(job.id, "failed")]
    j = await _job(job.id)
    assert j.status == "failed" and "Worker lost 2 times" in j.error

    # A fresh heartbeat protects a running job from the reaper.
    job2 = await svc.create_job(Q, "quick")
    assert await alive.claim() == job2.id
    assert await alive.reap() == []


async def test_worker_shutdown_returns_job_to_queue_and_another_worker_finishes_it(db, worker_mode, monkeypatch):
    import app.orchestration.runner as runner
    from app.retrieval.search import get_search_provider

    gate = {"slow": True}
    real = get_search_provider()

    class GatedSearch:
        name = "gated"

        async def search(self, q, n=5):
            if gate["slow"]:
                await asyncio.sleep(30)
            return await real.search(q, n)

    monkeypatch.setattr(runner, "get_search_provider", lambda: GatedSearch())
    job = await svc.create_job(Q, "quick")
    w1 = Worker("w1", JobManager())
    await w1.fill()
    for _ in range(100):
        j = await _job(job.id)
        if j.stage == "research":
            break
        await asyncio.sleep(0.05)
    await w1.manager.shutdown()  # SIGTERM path

    j = await _job(job.id)
    assert j.status == "queued" and j.worker_id is None and j.completed_at is None
    assert j.stage == "research"  # progress kept: resumes here, not from 'plan'

    gate["slow"] = False
    w2 = Worker("w2", JobManager())
    assert await w2.fill() == 1
    await _drain(w2.manager)
    j = await _job(job.id)
    assert j.status == "completed", j.error
    assert j.run_attempts == 2


async def test_resume_refuses_queued_jobs(db, worker_mode):
    job = await svc.create_job(Q, "quick")
    with pytest.raises(ConflictError):
        await job_manager.resume(job.id)
