from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.agents.reporter import report_markdown
from app.core import demo
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.database.session import get_session, session_scope
from app.models import Claim, Finding, FollowUp, Report, ResearchJob, ResearchTask, Source
from app.orchestration.depth import get_depth
from app.orchestration.events import event_bus
from app.orchestration.runner import TERMINAL, job_manager
from app.schemas.api import (
    ClaimOut,
    CreateResearch,
    DeepenRequest,
    FindingOut,
    FollowUpOut,
    FollowUpRequest,
    JobDetail,
    JobSummary,
    ReportOut,
    SourceOut,
    SourceWithText,
    TaskOut,
)
from app.services import research as svc
from app.services.export import report_html

router = APIRouter(prefix="/api/research", tags=["research"])


async def _job(session: AsyncSession, job_id: uuid.UUID) -> ResearchJob:
    job = await session.get(ResearchJob, job_id)
    if job is None:
        raise NotFoundError("Research job not found")
    return job


@router.post("", response_model=JobSummary, status_code=202)
async def create_research(body: CreateResearch, request: Request):
    """Start an autonomous research job. Progress streams from `/events`."""
    async with session_scope() as s:
        key = await demo.check_new_job(s, request, body.depth, get_depth(body.depth).max_cost_usd)
    return await svc.create_job(body.question, body.depth, body.document_ids, client_key=key)


@router.get("", response_model=list[JobSummary])
async def list_research(
    request: Request,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
):
    stmt = select(ResearchJob).order_by(ResearchJob.created_at.desc()).limit(limit).offset(offset)
    if get_settings().demo_mode:
        # Public demo: visitors see only their own sessions, never other people's questions.
        stmt = stmt.where(ResearchJob.client_key == demo.client_key(request))
    if status:
        stmt = stmt.where(ResearchJob.status == status)
    return list((await session.execute(stmt)).scalars())


@router.get("/{job_id}", response_model=JobDetail)
async def get_research(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job = (await session.execute(
        select(ResearchJob).options(selectinload(ResearchJob.tasks)).where(ResearchJob.id == job_id)
    )).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Research job not found")
    counts = {
        "sources": await session.scalar(select(func.count()).select_from(Source).where(Source.collection == f"job:{job_id}")),
        "findings": await session.scalar(select(func.count()).select_from(Finding).where(Finding.job_id == job_id)),
        "claims": dict((await session.execute(
            select(Claim.status, func.count()).where(Claim.job_id == job_id).group_by(Claim.status)
        )).all()),
        "reports": await session.scalar(select(func.count()).select_from(Report).where(Report.job_id == job_id)),
    }
    return JobDetail.model_validate({**JobSummary.model_validate(job).model_dump(), "limits": job.limits, "analysis": job.analysis,
                                     "started_at": job.started_at, "tasks": job.tasks, "counts": counts})


@router.delete("/{job_id}", status_code=204)
async def delete_research(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job = await _job(session, job_id)
    if job_manager.is_running(job_id) or job.status in ("running", "queued"):
        raise ConflictError("Cancel the job before deleting it")
    await session.execute(delete(Source).where(Source.collection == f"job:{job_id}"))
    await session.delete(job)
    return Response(status_code=204)


@router.post("/{job_id}/cancel", status_code=202)
async def cancel_research(job_id: uuid.UUID):
    await job_manager.cancel(job_id)
    return {"status": "cancelling"}


@router.post("/{job_id}/resume", status_code=202)
async def resume_research(job_id: uuid.UUID):
    await job_manager.resume(job_id)
    return {"status": "queued"}


@router.post("/{job_id}/deepen", response_model=JobSummary, status_code=202)
async def deepen_research(job_id: uuid.UUID, body: DeepenRequest):
    demo.forbid_in_demo("'Investigate further'")
    return await svc.deepen(job_id, body.instructions)


@router.get("/{job_id}/events")
async def stream_events(job_id: uuid.UUID, request: Request, after: int = 0, session: AsyncSession = Depends(get_session)):
    """Server-Sent Events. Replays past events (resume with Last-Event-ID) then streams live ones."""
    await _job(session, job_id)
    await session.close()
    last = int(request.headers.get("last-event-id") or after or 0)

    async def gen():
        nonlocal last
        while True:
            if await request.is_disconnected():
                return
            events = await event_bus.since(job_id, last)
            for ev in events:
                last = ev.id
                yield {"id": str(ev.id), "event": "progress", "data": json.dumps({
                    "id": ev.id, "type": ev.type, "stage": ev.stage, "message": ev.message, "data": ev.data,
                    "created_at": ev.created_at.isoformat()})}
            if not events:
                async with session_scope() as s:
                    status = await s.scalar(select(ResearchJob.status).where(ResearchJob.id == job_id))
                if status in TERMINAL and not job_manager.is_running(job_id):
                    yield {"event": "end", "data": json.dumps({"status": status})}
                    return
                await event_bus.wait(job_id, timeout=2.0)

    return EventSourceResponse(gen(), ping=15)


@router.get("/{job_id}/sources", response_model=list[SourceOut])
async def list_sources(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _job(session, job_id)
    return list((await session.execute(
        select(Source).where(Source.collection == f"job:{job_id}").order_by(Source.retrieved_at)
    )).scalars())


@router.get("/{job_id}/sources/{source_id}", response_model=SourceWithText)
async def get_source(job_id: uuid.UUID, source_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    src = await session.get(Source, source_id)
    if src is None or src.collection != f"job:{job_id}":
        raise NotFoundError("Source not found")
    return src


@router.get("/{job_id}/findings", response_model=list[FindingOut])
async def list_findings(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _job(session, job_id)
    return list((await session.execute(select(Finding).where(Finding.job_id == job_id).order_by(Finding.created_at))).scalars())


@router.get("/{job_id}/claims", response_model=list[ClaimOut])
async def list_claims(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _job(session, job_id)
    return list((await session.execute(
        select(Claim).options(selectinload(Claim.evidence)).where(Claim.job_id == job_id).order_by(Claim.importance, Claim.created_at)
    )).scalars())


@router.get("/{job_id}/tasks", response_model=list[TaskOut])
async def list_tasks(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _job(session, job_id)
    return list((await session.execute(select(ResearchTask).where(ResearchTask.job_id == job_id).order_by(ResearchTask.created_at))).scalars())


async def _report(session: AsyncSession, job_id: uuid.UUID, version: int | None) -> tuple[Report, list[int]]:
    await _job(session, job_id)
    versions = list((await session.execute(select(Report.version).where(Report.job_id == job_id).order_by(Report.version))).scalars())
    if not versions:
        raise NotFoundError("No report has been generated yet")
    v = version or versions[-1]
    rep = (await session.execute(select(Report).where(Report.job_id == job_id, Report.version == v))).scalar_one_or_none()
    if rep is None:
        raise NotFoundError(f"Report version {v} not found")
    return rep, versions


@router.get("/{job_id}/report", response_model=ReportOut)
async def get_report(job_id: uuid.UUID, version: int | None = None, session: AsyncSession = Depends(get_session)):
    rep, versions = await _report(session, job_id, version)
    out = ReportOut.model_validate(rep)
    out.versions = versions
    return out


@router.get("/{job_id}/report/export")
async def export_report(job_id: uuid.UUID, format: str = Query("md", pattern="^(md|html|json)$"), version: int | None = None,
                        session: AsyncSession = Depends(get_session)):
    rep, _ = await _report(session, job_id, version)
    name = f"scout-report-{str(job_id)[:8]}-v{rep.version}"
    if format == "md":
        return PlainTextResponse(report_markdown(rep), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    if format == "html":
        return Response(report_html(rep), media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{name}.html"'})
    return Response(ReportOut.model_validate(rep).model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}.json"'})


@router.post("/{job_id}/report/sections/{key}/regenerate", response_model=ReportOut)
async def regenerate_section(job_id: uuid.UUID, key: str):
    demo.forbid_in_demo("Section regeneration")
    return await svc.regenerate_section(job_id, key)


@router.get("/{job_id}/followups", response_model=list[FollowUpOut])
async def list_followups(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _job(session, job_id)
    return list((await session.execute(select(FollowUp).where(FollowUp.job_id == job_id).order_by(FollowUp.created_at))).scalars())


@router.post("/{job_id}/followups", response_model=FollowUpOut, status_code=201)
async def create_followup(job_id: uuid.UUID, body: FollowUpRequest, session: AsyncSession = Depends(get_session)):
    job = await _job(session, job_id)
    if job.status not in ("completed",):
        raise ConflictError("Follow-up questions are available once research has completed")
    await session.close()
    await demo.check_budget(session)
    return await svc.ask_followup(job_id, body.question)
