"""Document library (user uploads) and standalone RAG endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ExtractionError, NotFoundError, ValidationFailed
from app.database.session import get_session
from app.llm.factory import get_embedder, get_llm
from app.models import Chunk, Source
from app.rag.ingestion import IngestionService
from app.rag.qa import grounded_answer
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.retrieval.extract import extract_html, extract_pdf, extract_plain
from app.schemas.api import ChunkHit, DocumentOut, RagQuery, RagSearch

router = APIRouter(prefix="/api", tags=["documents & rag"])

ALLOWED = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown", ".html": "text/html", ".htm": "text/html"}
LIBRARY = "library"


def _scope(job_id: uuid.UUID | None) -> str:
    return f"job:{job_id}" if job_id else LIBRARY


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Upload a PDF / text / Markdown / HTML document into the library. The file is parsed in memory;
    only extracted text is stored. Nothing in it is ever executed."""
    s = get_settings()
    name = Path(file.filename or "upload").name[:200]
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ValidationFailed(f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED))}")
    limit = s.upload_max_mb * 1024 * 1024
    body = await file.read(limit + 1)
    if len(body) > limit:
        raise ValidationFailed(f"File exceeds the {s.upload_max_mb} MB limit")
    if not body:
        raise ValidationFailed("File is empty")
    if ext == ".pdf" and not body.startswith(b"%PDF-"):
        raise ValidationFailed("File content is not a valid PDF")
    if ext != ".pdf" and b"\x00" in body[:4096]:
        raise ValidationFailed("Binary content is not allowed for text documents")
    try:
        doc = extract_pdf(body) if ext == ".pdf" else extract_html(body) if ext in (".html", ".htm") else extract_plain(body)
    except ExtractionError as e:
        raise ValidationFailed(str(e)) from e
    res = await IngestionService(get_embedder()).ingest(
        session, collection=LIBRARY, text=doc.text, title=doc.title or name, source_type="upload",
        published_at=doc.published_at, extraction_method=doc.method,
    )
    n = await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == res.source.id))
    return DocumentOut.model_validate(res.source).model_copy(update={"chunk_count": n or 0})


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Source, func.count(Chunk.id)).outerjoin(Chunk, Chunk.source_id == Source.id)
        .where(Source.collection == LIBRARY).group_by(Source.id).order_by(Source.retrieved_at.desc())
    )).all()
    return [DocumentOut.model_validate(src).model_copy(update={"chunk_count": n}) for src, n in rows]


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    src = await session.get(Source, doc_id)
    if src is None or src.collection != LIBRARY:
        raise NotFoundError("Document not found")
    await session.delete(src)
    return Response(status_code=204)


@router.post("/rag/search", response_model=list[ChunkHit])
async def rag_search(body: RagSearch, session: AsyncSession = Depends(get_session)):
    """Raw retrieval (no generation): useful for debugging and retrieval evaluation."""
    f = RetrievalFilters(collections=[_scope(body.job_id)], source_ids=body.document_ids, source_types=body.source_types, domains=body.domains)
    hits = await HybridRetriever(get_embedder()).search(session, body.question, f, k=body.k, mode=body.mode)
    return [ChunkHit(chunk_id=h.chunk_id, source_id=h.source_id, title=h.title, url=h.url, domain=h.domain, score=h.score,
                     vector_similarity=h.vector_similarity, keyword_rank=h.keyword_rank, text=h.text) for h in hits]


@router.post("/rag/query")
async def rag_query(body: RagQuery, session: AsyncSession = Depends(get_session)):
    """Grounded question answering over the library (or a research job's sources)."""
    f = RetrievalFilters(collections=[_scope(body.job_id)], source_ids=body.document_ids)
    res = await grounded_answer(session, get_llm(), HybridRetriever(get_embedder()), body.question, f, k=body.k, mode=body.mode)
    return {"answer": res.answer, "insufficient_evidence": res.insufficient_evidence, "citations": [c.__dict__ for c in res.citations],
            "retrieved_chunks": res.retrieved, "removed_invalid_citations": res.removed_citations}
