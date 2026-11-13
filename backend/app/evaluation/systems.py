"""The three systems compared by the evaluation: direct LLM, single-pass RAG, autonomous agent."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.errors import ScoutError
from app.database.session import session_scope
from app.llm.factory import get_embedder, get_llm
from app.llm.prompts import DIRECT_SYSTEM
from app.llm.usage import UsageTracker, bind_usage, unbind_usage
from app.models import Report, ResearchJob, ResearchTask
from app.rag.ingestion import IngestionService
from app.rag.qa import grounded_answer
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.retrieval.extract import extract_by_type
from app.retrieval.fetcher import get_fetcher
from app.retrieval.search import get_search_provider
from app.schemas.llm_outputs import DirectAnswer


@dataclass
class SystemOutput:
    system: str
    answer: str = ""
    collection: str | None = None  # retrieval scope used, for faithfulness judging
    label_to_source: dict = field(default_factory=dict)
    cited_urls: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str | None = None
    agent: dict = field(default_factory=dict)


async def run_direct(question: str) -> SystemOutput:
    out = SystemOutput("direct_llm")
    t = UsageTracker()
    tok = bind_usage(t)
    t0 = time.monotonic()
    try:
        r = await get_llm().structured(task="direct_answer", system=DIRECT_SYSTEM, user=question, schema=DirectAnswer, inputs={"question": question})
        out.answer = r.answer_markdown
    except ScoutError as e:
        out.error = str(e)
    finally:
        unbind_usage(tok)
    out.latency_s, out.usage = time.monotonic() - t0, t.snapshot()
    return out


async def run_rag(question: str, results: int = 5) -> SystemOutput:
    """One search, fetch + ingest top results, retrieve, answer with citations. No planning,
    no iteration, no verification."""
    out = SystemOutput("single_pass_rag", collection=f"eval:rag:{uuid.uuid4()}")
    t = UsageTracker()
    tok = bind_usage(t)
    t0 = time.monotonic()
    try:
        fetcher, ingest = get_fetcher(), IngestionService(get_embedder())
        hits = await get_search_provider().search(question, results)
        t.record_tool("search", True)
        for h in hits:
            try:
                doc = await fetcher.fetch(h.url)
                ext = extract_by_type(doc.body, doc.content_type, doc.url)
                t.record_tool("fetch", True)
            except ScoutError:
                t.record_tool("fetch", False)
                continue
            async with session_scope() as s:
                await ingest.ingest(s, collection=out.collection, text=ext.text, title=ext.title or h.title, source_type="web",
                                    url=doc.url, published_at=ext.published_at, extraction_method=ext.method)
        async with session_scope() as s:
            res = await grounded_answer(s, get_llm(), HybridRetriever(get_embedder()), question, RetrievalFilters(collections=[out.collection]))
        out.answer = res.answer
        out.label_to_source = {c.label: uuid.UUID(c.source_id) for c in res.citations}
        out.cited_urls = [c.url for c in res.citations if c.url]
    except ScoutError as e:
        out.error = str(e)
    finally:
        unbind_usage(tok)
    out.latency_s, out.usage = time.monotonic() - t0, t.snapshot()
    return out


async def run_agent(question: str, depth: str) -> SystemOutput:
    from app.orchestration.runner import job_manager
    from app.services.research import create_job

    out = SystemOutput("autonomous_agent")
    t0 = time.monotonic()
    job = await create_job(question, depth)
    task = job_manager._tasks.get(job.id)
    if task:
        await task
    out.latency_s = time.monotonic() - t0
    async with session_scope() as s:
        j = await s.get(ResearchJob, job.id)
        rep = (await s.execute(select(Report).where(Report.job_id == job.id).order_by(Report.version.desc()))).scalars().first()
        tasks = list((await s.execute(select(ResearchTask).where(ResearchTask.job_id == job.id))).scalars())
    out.collection, out.usage = f"job:{job.id}", j.usage
    if j.status != "completed" or rep is None:
        out.error = j.error or f"job {j.status}"
    else:
        secs = {sec["key"]: sec["markdown"] for sec in rep.sections}
        out.answer = secs.get("executive_summary", "") + "\n\n" + secs.get("key_findings", "")
        out.label_to_source = {r["label"]: uuid.UUID(r["source_id"]) for r in rep.references}
        used = set(re.findall(r"\[(S\d+)\]", out.answer))
        out.cited_urls = [r["url"] for r in rep.references if r["label"] in used and r["url"]]
    u = j.usage or {}
    out.agent = {
        "job_id": str(job.id),
        "status": j.status,
        "tasks": len(tasks),
        "task_completion_rate": (sum(t.status == "completed" for t in tasks) / len(tasks)) if tasks else 0.0,
        "failed_task_rate": (sum(t.status == "failed" for t in tasks) / len(tasks)) if tasks else 0.0,
        "research_rounds": j.iteration + 1,
        "tool_call_success_rate": (1 - u.get("tool_failures", 0) / u["tool_calls"]) if u.get("tool_calls") else None,
        "halt_reason": j.halt_reason,
        "claims_by_status": (rep.citation_stats or {}).get("claims_by_status", {}) if rep else {},
    }
    return out
