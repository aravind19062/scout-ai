"""Grounded question answering over a retrieval scope (usable without the agent workflow)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.gateway import LLMGateway
from app.llm.prompts import QA_SYSTEM
from app.rag.citations import validate_citations
from app.rag.context import assemble_context
from app.rag.retriever import HybridRetriever, Mode, RetrievalFilters
from app.schemas.llm_outputs import GroundedAnswer


@dataclass
class Citation:
    label: str
    source_id: str
    title: str
    url: str | None
    quote_context: str


@dataclass
class QAResult:
    answer: str
    insufficient_evidence: bool
    citations: list[Citation]
    retrieved: int
    removed_citations: list[str]


async def grounded_answer(
    session: AsyncSession,
    llm: LLMGateway,
    retriever: HybridRetriever,
    question: str,
    filters: RetrievalFilters,
    k: int = 10,
    mode: Mode | None = None,
    extra_context: str = "",
    max_context_tokens: int = 6000,
) -> QAResult:
    chunks = await retriever.search(session, question, filters, k=k, mode=mode)
    ctx = assemble_context(chunks, max_tokens=max_context_tokens, label_prefix="S", per_source_labels=True)
    if not ctx.passages:
        return QAResult("No relevant sources were found for this question in the available documents.", True, [], 0, [])
    user = (
        (f"Background (report summary written earlier, for orientation only; cite passages, not this):\n{extra_context}\n\n" if extra_context else "")
        + f"Passages:\n{ctx.text}\n\nQuestion: {question}"
    )
    out = await llm.structured(
        task="grounded_answer", system=QA_SYSTEM, user=user, schema=GroundedAnswer,
        inputs={"question": question, "passages": [{"id": p.label, "text": p.chunk.text} for p in ctx.passages]},
    )
    labels = {p.label for p in ctx.passages}
    check = validate_citations(out.answer_markdown, labels)
    first_by_label: dict[str, object] = {}
    for p in ctx.passages:
        first_by_label.setdefault(p.label, p.chunk)
    citations = [
        Citation(label=lbl, source_id=str(first_by_label[lbl].source_id), title=first_by_label[lbl].title,
                 url=first_by_label[lbl].url, quote_context=first_by_label[lbl].text[:300])
        for lbl in check.used
    ]
    insufficient = out.insufficient_evidence or not check.used
    return QAResult(check.text, insufficient, citations, len(chunks), check.removed)
