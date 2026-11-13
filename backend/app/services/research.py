"""Application services used by the API layer."""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import persist_tasks
from app.agents.reporter import LLM_SECTIONS, generate_report
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.database.session import session_scope
from app.llm.usage import bind_usage, unbind_usage
from app.models import FollowUp, ResearchJob, Source
from app.orchestration.depth import get_depth
from app.orchestration.runner import build_context, job_manager
from app.rag.qa import grounded_answer
from app.rag.retriever import RetrievalFilters
from app.schemas.llm_outputs import PlannedTask


async def create_job(question: str, depth: str, document_ids: list[uuid.UUID] | None = None,
                     client_key: str | None = None) -> ResearchJob:
    cfg = get_depth(depth)
    async with session_scope() as s:
        job = ResearchJob(question=question.strip(), depth=depth, limits=cfg.as_dict(), provider_mode=get_settings().mode_summary,
                          client_key=client_key)
        s.add(job)
        await s.flush()
        if document_ids:
            await _attach_documents(s, job, document_ids)
    job_manager.start(job.id)
    return job


async def _attach_documents(s: AsyncSession, job: ResearchJob, document_ids: list[uuid.UUID]) -> None:
    """Copy uploaded library documents into the job's evidence collection (embeddings are reused)."""
    from app.llm.factory import get_embedder
    from app.rag.ingestion import IngestionService

    svc = IngestionService(get_embedder())
    docs = list((await s.execute(select(Source).where(Source.id.in_(document_ids), Source.collection == "library"))).scalars())
    if len(docs) != len(set(document_ids)):
        raise NotFoundError("One or more documents were not found in the library")
    for d in docs:
        await svc.ingest(s, collection=f"job:{job.id}", job_id=job.id, text=d.text, title=d.title, source_type="upload",
                         url=d.url, published_at=d.published_at, extraction_method=d.extraction_method)


async def _load_job(job_id: uuid.UUID) -> ResearchJob:
    async with session_scope() as s:
        job = await s.get(ResearchJob, job_id)
    if job is None:
        raise NotFoundError("Research job not found")
    return job


async def ask_followup(job_id: uuid.UUID, question: str) -> FollowUp:
    job = await _load_job(job_id)
    ctx = build_context(job, limited=False)
    from app.models import Report

    async with session_scope() as s:
        report = (await s.execute(select(Report).where(Report.job_id == job_id).order_by(Report.version.desc()).limit(1))).scalar_one_or_none()
    summary = ""
    if report:
        summary = next((sec["markdown"] for sec in report.sections if sec["key"] == "executive_summary"), "")[:3000]
    ctx.usage.stage = "followup"
    token = bind_usage(ctx.usage)
    try:
        async with session_scope() as s:
            res = await grounded_answer(s, ctx.llm, ctx.retriever, question, RetrievalFilters(collections=[ctx.collection]), extra_context=summary)
    finally:
        unbind_usage(token)
    async with session_scope() as s:
        fu = FollowUp(job_id=job_id, question=question, answer=res.answer, insufficient_evidence=res.insufficient_evidence,
                      citations=[c.__dict__ for c in res.citations])
        s.add(fu)
        j = await s.get(ResearchJob, job_id)
        j.usage = ctx.usage.snapshot()
    return fu


async def deepen(job_id: uuid.UUID, instructions: str) -> ResearchJob:
    """Extend a finished job with additional targeted research, then re-analyse, re-verify and re-report."""
    job = await _load_job(job_id)
    if job_manager.is_running(job_id) or job.status in ("running", "queued"):
        raise ConflictError("Job is still running")
    base = get_depth(job.depth)
    # Grant one extra round worth of budget on top of what has been used.
    new_limits = replace(
        base,
        max_iterations=job.iteration + 2,
        max_tool_calls=(job.usage or {}).get("tool_calls", 0) + base.max_tool_calls // 2,
        max_tokens=(job.usage or {}).get("total_tokens", 0) + base.max_tokens // 2,
        max_cost_usd=(job.usage or {}).get("cost_usd", 0) + base.max_cost_usd / 2,
        max_duration_s=int((job.usage or {}).get("elapsed_s", 0)) + base.max_duration_s // 2,
    )
    async with session_scope() as s:
        j = await s.get(ResearchJob, job_id)
        j.limits = new_limits.as_dict()
        j.iteration += 1
        j.halt_reason, j.status, j.stage, j.cancel_requested, j.completed_at = None, "queued", "research", False, None
    job = await _load_job(job_id)
    ctx = build_context(job)
    planned = [PlannedTask(objective=f"Deeper investigation: {instructions}", search_queries=[instructions, f"{job.question[:150]} {instructions}"],
                           expected_evidence="Specific, sourced evidence", priority=1)]
    token = bind_usage(ctx.usage)
    try:
        await persist_tasks(ctx, planned, origin="followup", iteration=job.iteration, limit=1)
    finally:
        unbind_usage(token)
    job_manager.start(job_id)
    return job


async def regenerate_section(job_id: uuid.UUID, key: str):
    if key not in LLM_SECTIONS:
        raise ConflictError(f"Section '{key}' is generated from data and cannot be regenerated; valid: {sorted(LLM_SECTIONS)}")
    job = await _load_job(job_id)
    if job.status != "completed":
        raise ConflictError("Report sections can only be regenerated for completed research")
    ctx = build_context(job, limited=False)
    # Bypass the LLM cache for regeneration, otherwise the identical prompt returns the identical text.
    from app.llm.gateway import LLMGateway

    ctx.llm = LLMGateway(ctx.llm.provider, ctx.llm.settings, use_cache=False)
    ctx.usage.stage = "report"
    token = bind_usage(ctx.usage)
    try:
        report = await generate_report(ctx, only=key)
    finally:
        unbind_usage(token)
    async with session_scope() as s:
        j = await s.get(ResearchJob, job_id)
        j.usage = ctx.usage.snapshot()
    return report
