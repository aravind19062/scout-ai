"""Research agent: executes one task (search -> fetch -> extract -> ingest -> retrieve -> extract findings)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import (
    BudgetExceeded,
    ExtractionError,
    FetchError,
    JobCancelled,
    ProviderError,
    UnsafeURLError,
)
from app.core.logging import get_logger
from app.core.text import normalize, quote_in_text
from app.database.base import utcnow
from app.database.session import session_scope
from app.llm.prompts import RESEARCHER_SYSTEM
from app.models import Finding, ResearchTask, Source
from app.orchestration.context import JobContext
from app.rag.context import assemble_context
from app.retrieval.extract import extract_by_type
from app.retrieval.search import SearchResult
from app.schemas.llm_outputs import FindingsExtraction

log = get_logger(__name__)
NON_RETRYABLE = (BudgetExceeded, JobCancelled, asyncio.CancelledError)


class UrlRegistry:
    """Job-wide set of URLs already fetched/claimed, shared by concurrently running tasks."""

    def __init__(self, urls: set[str]):
        self._urls = set(urls)
        self._lock = asyncio.Lock()

    async def claim(self, url: str) -> bool:
        async with self._lock:
            if url in self._urls:
                return False
            self._urls.add(url)
            return True


async def _search(ctx: JobContext, task: ResearchTask, query: str) -> list[SearchResult]:
    await ctx.checkpoint()
    try:
        results = await ctx.search.search(query, ctx.depth.results_per_query)
        ctx.usage.record_tool("search", True)
    except ProviderError as e:
        ctx.usage.record_tool("search", False)
        await ctx.emit("search_failed", f"[{task.key}] Search failed: {e}", "research", task=task.key, query=query)
        return []
    await ctx.emit(
        "search", f"[{task.key}] Searched “{query}” — {len(results)} results", "research",
        task=task.key, query=query, provider=ctx.search.name,
        results=[{"title": r.title, "url": r.url} for r in results],
    )
    return results


async def _acquire(ctx: JobContext, task: ResearchTask, r: SearchResult, query: str) -> Source | None:
    await ctx.checkpoint()
    try:
        doc = await ctx.fetcher.fetch(r.url)
        ctx.usage.record_tool("fetch", True)
        extracted = extract_by_type(doc.body, doc.content_type, doc.url)
    except (FetchError, UnsafeURLError, ExtractionError) as e:
        if isinstance(e, ExtractionError):
            ctx.usage.record_tool("fetch", True)  # fetched fine, nothing usable
        else:
            ctx.usage.record_tool("fetch", False)
        await ctx.emit("source_failed", f"[{task.key}] Could not use {r.url}: {e}", "research", task=task.key, url=r.url)
        return None
    try:
        async with session_scope() as s:
            res = await ctx.ingestion.ingest(
                s, collection=ctx.collection, job_id=ctx.job_id, task_id=task.id, url=doc.url,
                title=extracted.title or r.title, text=extracted.text,
                source_type="pdf" if doc.content_type == "application/pdf" else "web",
                published_at=extracted.published_at or r.published_at, extraction_method=extracted.method,
                search_query=query,
            )
    except IntegrityError:  # identical content ingested concurrently by another task
        await ctx.emit("source_duplicate", f"[{task.key}] Duplicate content skipped: {r.url}", "research", task=task.key, url=r.url)
        return None
    if not res.created:
        await ctx.emit("source_duplicate", f"[{task.key}] Duplicate content skipped: {r.url}", "research", task=task.key, url=r.url)
        return None
    src = res.source
    await ctx.emit(
        "source_added", f"[{task.key}] Added source: {src.title}", "research",
        task=task.key, source_id=str(src.id), url=src.url, title=src.title, domain=src.domain,
        published_at=src.published_at, chunks=res.chunks_created, security_flags=src.security_flags,
    )
    return src


async def _extract_findings(ctx: JobContext, task: ResearchTask) -> tuple[int, int, str]:
    d = ctx.depth
    query = task.objective + " " + " ".join(task.queries)
    async with session_scope() as s:
        chunks = await ctx.retriever.search(s, query, ctx.filters(), k=d.passages_per_task * 2)
    context = assemble_context(chunks, max_tokens=7000, label_prefix="P", max_per_source=2)
    passages = context.passages[: d.passages_per_task]
    if not passages:
        return 0, 0, "No relevant source content was found for this task."
    by_label = {p.label: p.chunk for p in passages}
    body = "\n\n".join(_render(p) for p in passages)
    user = (
        f"Research objective: {task.objective}\nExpected evidence: {task.expected_evidence}\n\n"
        f"Passages:\n{body}\n\nExtract up to {d.findings_per_task} findings."
    )
    out = await ctx.llm.structured(
        task="extract_findings", system=RESEARCHER_SYSTEM, user=user, schema=FindingsExtraction,
        inputs={"objective": task.objective, "max_findings": d.findings_per_task,
                "passages": [{"id": p.label, "text": p.chunk.text} for p in passages]},
    )
    kept, rejected, duplicates = 0, 0, 0
    async with session_scope() as s:
        seen = {
            (sid, normalize(q))
            for sid, q in (await s.execute(select(Finding.source_id, Finding.evidence_quote).where(Finding.job_id == ctx.job_id))).all()
        }
        for f in out.findings[: d.findings_per_task]:
            chunk = by_label.get(f.passage_id.strip())
            # Guard against fabricated evidence: the quote must exist verbatim in the cited passage.
            if chunk is None or not quote_in_text(f.evidence_quote, chunk.text):
                rejected += 1
                continue
            key = (chunk.source_id, normalize(f.evidence_quote))
            if key in seen:  # same evidence already captured by another task
                duplicates += 1
                continue
            seen.add(key)
            s.add(Finding(
                job_id=ctx.job_id, task_id=task.id, source_id=chunk.source_id, chunk_id=chunk.chunk_id,
                statement=f.statement.strip(), evidence_quote=f.evidence_quote.strip(), kind=f.kind, quote_verified=True,
            ))
            kept += 1
    return kept, rejected, out.task_summary


def _render(p) -> str:
    from app.llm.prompts import passage

    c = p.chunk
    return passage(p.label, c.text, f"{c.title} | {c.domain or c.source_type} | published: {c.published_at or 'unknown'}")


async def run_task(ctx: JobContext, task_id: uuid.UUID, urls: UrlRegistry) -> None:
    async with session_scope() as s:
        task = await s.get(ResearchTask, task_id)
        assert task is not None
        task.status, task.attempts, task.error = "running", task.attempts + 1, None
    await ctx.emit("task_started", f"[{task.key}] {task.objective}", "research", task=task.key)

    new_sources = 0
    for query in task.queries:
        for r in await _search(ctx, task, query):
            if await urls.claim(r.url) and await _acquire(ctx, task, r, query):
                new_sources += 1

    kept, rejected, summary = await _extract_findings(ctx, task)
    async with session_scope() as s:
        t = await s.get(ResearchTask, task_id)
        t.status, t.summary, t.completed_at = "completed", summary, utcnow()
    await ctx.emit(
        "task_completed",
        f"[{task.key}] {kept} findings from {new_sources} new sources" + (f"; {rejected} findings rejected (quote not found in source)" if rejected else ""),
        "research", task=task.key, findings=kept, rejected_findings=rejected, new_sources=new_sources,
    )


async def run_task_with_retries(ctx: JobContext, task_id: uuid.UUID, urls: UrlRegistry) -> None:
    attempts = ctx.depth.task_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            await run_task(ctx, task_id, urls)
            return
        except NON_RETRYABLE:
            async with session_scope() as s:
                t = await s.get(ResearchTask, task_id)
                if t and t.status == "running":
                    t.status = "pending"  # resumable
            raise
        except Exception as e:
            log.warning("task.failed", task_id=str(task_id), attempt=attempt, error=str(e))
            final = attempt == attempts
            async with session_scope() as s:
                t = await s.get(ResearchTask, task_id)
                t.status = "failed" if final else "pending"
                t.error = f"{type(e).__name__}: {e}"[:1000]
                key = t.key
            await ctx.emit(
                "task_failed" if final else "task_retry",
                f"[{key}] {'Failed' if final else 'Retrying'} after error: {type(e).__name__}", "research",
                task=key, attempt=attempt,
            )
            if not final:
                await asyncio.sleep(min(2**attempt, 10) if ctx.llm.provider.name != "mock" else 0)


async def run_pending_tasks(ctx: JobContext) -> None:
    async with session_scope() as s:
        tasks = list((await s.execute(
            select(ResearchTask).where(ResearchTask.job_id == ctx.job_id, ResearchTask.status.in_(["pending", "running"]))
            .order_by(ResearchTask.priority, ResearchTask.created_at)
        )).scalars())
        known = set((await s.execute(select(Source.url).where(Source.collection == ctx.collection, Source.url.is_not(None)))).scalars())
    urls = UrlRegistry(known)
    sem = asyncio.Semaphore(ctx.depth.concurrency)

    async def guarded(tid):
        async with sem:
            await run_task_with_retries(ctx, tid, urls)

    # TaskGroup: if a budget/cancel error escapes one task, siblings are cancelled cleanly.
    try:
        async with asyncio.TaskGroup() as tg:
            for t in tasks:
                tg.create_task(guarded(t.id))
    except ExceptionGroup as eg:
        # Surface control-flow signals (cancel > budget) as plain exceptions for the orchestrator.
        for kind in (JobCancelled, BudgetExceeded):
            sub = eg.subgroup(kind)
            if sub is not None:
                raise _leaf(sub) from None
        raise


def _leaf(eg: BaseExceptionGroup) -> BaseException:
    exc: BaseException = eg
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc
