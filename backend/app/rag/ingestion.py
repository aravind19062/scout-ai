"""Document ingestion: clean -> dedupe -> chunk -> embed (with reuse) -> store."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.text import sha256
from app.llm.embeddings import EmbeddingProvider
from app.models import Chunk, Source
from app.rag.chunking import chunk_text
from app.retrieval.extract import clean_text
from app.retrieval.safety import detect_injection

log = get_logger(__name__)


@dataclass
class IngestResult:
    source: Source
    created: bool  # False if an identical document already existed in the collection
    chunks_created: int
    embeddings_reused: int


class IngestionService:
    def __init__(self, embedder: EmbeddingProvider):
        self.embedder = embedder
        self.settings = get_settings()

    async def ingest(
        self,
        session: AsyncSession,
        *,
        collection: str,
        text: str,
        title: str,
        source_type: str,
        url: str | None = None,
        job_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        published_at: str | None = None,
        extraction_method: str = "",
        search_query: str | None = None,
    ) -> IngestResult:
        text = clean_text(text)
        content_hash = sha256(text)
        existing = (
            await session.execute(
                select(Source).where(Source.collection == collection, Source.content_hash == content_hash)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return IngestResult(existing, created=False, chunks_created=0, embeddings_reused=0)

        source = Source(
            collection=collection, job_id=job_id, task_id=task_id, url=url, title=title or (url or "Untitled"),
            domain=(urlsplit(url).hostname if url else None), source_type=source_type, published_at=published_at,
            content_hash=content_hash, char_count=len(text), extraction_method=extraction_method,
            search_query=search_query, security_flags=detect_injection(text), text=text,
        )
        session.add(source)
        await session.flush()
        if source.security_flags:
            log.warning("ingest.injection_flagged", url=url, flags=source.security_flags)

        pieces = chunk_text(text, self.settings.chunk_size_tokens, self.settings.chunk_overlap_tokens)
        hashes = [sha256(p.text) for p in pieces]

        # Reuse embeddings for identical chunk text already embedded with the same model (any collection).
        reuse: dict[str, list[float]] = {}
        if hashes:
            rows = await session.execute(
                select(Chunk.content_hash, Chunk.embedding)
                .where(Chunk.content_hash.in_(set(hashes)), Chunk.embedding_model == self.embedder.model)
            )
            reuse = {h: list(e) for h, e in rows.all()}
        to_embed = [p.text for p, h in zip(pieces, hashes, strict=True) if h not in reuse]
        new_vectors = iter(await self.embedder.embed(to_embed)) if to_embed else iter(())

        for piece, h in zip(pieces, hashes, strict=True):
            vec = reuse[h] if h in reuse else next(new_vectors)
            session.add(
                Chunk(
                    source_id=source.id, collection=collection, ordinal=piece.ordinal, text=piece.text,
                    token_count=piece.token_count, content_hash=h, embedding_model=self.embedder.model,
                    embedding=vec, meta={"title": source.title, "url": url, "source_type": source_type},
                )
            )
        await session.flush()
        return IngestResult(source, True, len(pieces), len(pieces) - len(to_embed))
