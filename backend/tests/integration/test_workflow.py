import asyncio

import pytest
from sqlalchemy import select

from app.core.text import quote_in_text
from app.database.session import session_scope
from app.llm.gateway import LLMGateway
from app.llm.mock_provider import MockProvider
from app.models import Chunk, Claim, ClaimEvidence, Finding, Report, ResearchEvent, ResearchJob, ResearchTask, Source
from app.orchestration.runner import job_manager
from app.services import research as svc

pytestmark = pytest.mark.integration

Q = ("Analyse the Freedonia electric vehicle market. Identify market size, major competitors, growth trends, "
     "government policy, battery technology developments, challenges, investment opportunities and future outlook.")


async def _wait(job_id, timeout=60):
    t = job_manager._tasks.get(job_id)
    if t:
        await asyncio.wait_for(asyncio.shield(t), timeout)
    async with session_scope() as s:
        return await s.get(ResearchJob, job_id)


async def _events(job_id):
    async with session_scope() as s:
        return list((await s.execute(select(ResearchEvent).where(ResearchEvent.job_id == job_id).order_by(ResearchEvent.id))).scalars())


def _use_provider(monkeypatch, provider):
    import app.orchestration.runner as runner

    gw = LLMGateway(provider)
    monkeypatch.setattr(runner, "get_llm", lambda: gw)
    return provider


async def test_full_research_workflow_mock(db):
    job = await svc.create_job(Q, "standard")
    job = await _wait(job.id)
    assert job.status == "completed", job.error
    assert job.stage == "done"

    async with session_scope() as s:
        tasks = list((await s.execute(select(ResearchTask).where(ResearchTask.job_id == job.id))).scalars())
        sources = list((await s.execute(select(Source).where(Source.job_id == job.id))).scalars())
        findings = list((await s.execute(select(Finding).where(Finding.job_id == job.id))).scalars())
        claims = list((await s.execute(select(Claim).where(Claim.job_id == job.id))).scalars())
        report = (await s.execute(select(Report).where(Report.job_id == job.id))).scalar_one()
        chunk_text = {c.id: c.text for c in (await s.execute(select(Chunk).where(Chunk.collection == f"job:{job.id}"))).scalars()}
        evidence = list((await s.execute(select(ClaimEvidence).join(Claim).where(Claim.job_id == job.id))).scalars())

    # Plan: multiple distinct tasks with queries.
    assert 4 <= len([t for t in tasks if t.origin == "plan"]) <= 6
    assert all(t.queries for t in tasks)
    assert all(t.status in ("completed", "failed") for t in tasks)
    # Sources: real fetched content with metadata; duplicates and unreachable pages are not stored.
    urls = {s.url for s in sources}
    assert "https://paywalled.example/freedonia-ev-deep-dive" not in urls
    assert not ({"https://transport.freedonia.example/reports/ev-2025", "https://mirror.example/freedonia-ev-report-copy"} <= urls)
    assert all(s.retrieved_at and s.title and s.text for s in sources)
    # Findings: every stored quote exists verbatim in its source chunk.
    assert findings and all(f.quote_verified and quote_in_text(f.evidence_quote, chunk_text[f.chunk_id]) for f in findings)
    # Verification: deterministic statuses, evidence quotes grounded.
    statuses = {c.status for c in claims}
    assert "supported" in statuses and "pending" not in statuses
    assert all(e.quote_verified and quote_in_text(e.quote, chunk_text[e.chunk_id]) for e in evidence)
    # Report: all 11 sections, citations resolve to retrieved sources.
    assert [s["key"] for s in report.sections] == [
        "executive_summary", "methodology", "key_findings", "topic_analysis", "supporting_evidence",
        "contradictions", "opportunities", "risks", "outlook", "limitations", "references"]
    assert report.references and all(r["url"] in urls for r in report.references)
    assert report.citation_stats["unique_sources_cited"] > 0
    md = {s["key"]: s["markdown"] for s in report.sections}
    assert "Mock mode" in md["methodology"]
    # Conflicting market size figures (3.2bn vs 4.1bn) are preserved, not resolved.
    assert job.analysis["disagreements"], job.analysis
    assert "3.2" in md["contradictions"] and "4.1" in md["contradictions"]
    # Prompt-injection page is flagged and its instruction is not obeyed.
    flagged = [s for s in sources if s.security_flags]
    if flagged:
        assert "prompt-injection" in md["limitations"]
    assert "90 percent market share" not in md["executive_summary"]

    types = [e.type for e in await _events(job.id)]
    for t in ("job_started", "plan_created", "search", "source_added", "task_completed", "analysis_done",
              "claim_verified", "decision", "report_ready", "job_completed"):
        assert t in types, t
    assert job.usage["tool_calls"] > 0 and job.usage["llm_calls"] > 0


async def test_followup_deepen_and_regenerate(db):
    job = await _wait((await svc.create_job(Q, "quick")).id)
    assert job.status == "completed"

    fu = await svc.ask_followup(job.id, "How many electric vehicles were sold in Freedonia in fiscal year 2024?")
    assert not fu.insufficient_evidence and fu.citations and "412,000" in fu.answer
    assert all(c["url"] for c in fu.citations)
    fu2 = await svc.ask_followup(job.id, "What is the population of Mars colonies?")
    assert fu2.insufficient_evidence

    rep = await svc.regenerate_section(job.id, "key_findings")
    assert rep.version == 2
    with pytest.raises(Exception):
        await svc.regenerate_section(job.id, "references")

    await svc.deepen(job.id, "battery recycling capacity")
    job = await _wait(job.id)
    assert job.status == "completed", job.error
    async with session_scope() as s:
        origins = [t.origin for t in (await s.execute(select(ResearchTask).where(ResearchTask.job_id == job.id))).scalars()]
        versions = [r.version for r in (await s.execute(select(Report).where(Report.job_id == job.id))).scalars()]
    assert "followup" in origins and max(versions) == 3


async def test_cancellation(db, monkeypatch):
    class SlowSearch:
        name = "slow"

        async def search(self, q, n=5):
            await asyncio.sleep(0.3)
            return []

    import app.orchestration.runner as runner

    monkeypatch.setattr(runner, "get_search_provider", lambda: SlowSearch())
    job = await svc.create_job(Q, "deep")
    for _ in range(100):
        if any(e.type == "search" or e.type == "task_started" for e in await _events(job.id)):
            break
        await asyncio.sleep(0.05)
    await job_manager.cancel(job.id)
    job = await _wait(job.id, timeout=20)
    assert job.status == "cancelled"
    assert "job_cancelled" in [e.type for e in await _events(job.id)]
    async with session_scope() as s:
        assert not (await s.execute(select(Report).where(Report.job_id == job.id))).scalars().all()


async def test_budget_limit_halts_gracefully(db, monkeypatch):
    import app.orchestration.depth as depth
    from dataclasses import replace

    tiny = replace(depth.DEPTHS["standard"], max_tool_calls=3)
    monkeypatch.setitem(depth.DEPTHS, "standard", tiny)
    job = await _wait((await svc.create_job(Q, "standard")).id)
    assert job.status == "completed"
    assert job.halt_reason == "max_tool_calls"
    assert job.usage["tool_calls"] <= 4
    async with session_scope() as s:
        report = (await s.execute(select(Report).where(Report.job_id == job.id))).scalar_one()
    lim = next(s["markdown"] for s in report.sections if s["key"] == "limitations")
    assert "max_tool_calls" in lim


async def test_provider_failure_marks_tasks_failed_but_completes(db, monkeypatch):
    _use_provider(monkeypatch, MockProvider(fail_tasks={"extract_findings"}))
    job = await _wait((await svc.create_job(Q, "quick")).id)
    assert job.status == "completed"
    async with session_scope() as s:
        tasks = list((await s.execute(select(ResearchTask).where(ResearchTask.job_id == job.id))).scalars())
        report = (await s.execute(select(Report).where(Report.job_id == job.id))).scalar_one()
    assert tasks and all(t.status == "failed" and t.attempts == 2 for t in tasks)
    md = {s["key"]: s["markdown"] for s in report.sections}
    assert "did not produce verifiable evidence" in md["executive_summary"]
    assert "Tasks that failed" in md["limitations"]


async def test_failed_job_resumes_without_repeating_paid_work(db, monkeypatch):
    p1 = _use_provider(monkeypatch, MockProvider(fail_tasks={"judge_evidence"}))
    job = await _wait((await svc.create_job(Q, "quick")).id)
    assert job.status == "failed" and job.stage == "verify"
    assert "plan" in p1.calls

    p2 = _use_provider(monkeypatch, MockProvider())
    await job_manager.resume(job.id)
    job = await _wait(job.id)
    assert job.status == "completed", job.error
    # Resume started at 'verify': no re-planning, no re-research.
    assert "plan" not in p2.calls and "extract_findings" not in p2.calls
    assert "judge_evidence" in p2.calls
