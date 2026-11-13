"""Hybrid retrieval over pgvector (cosine) and Postgres full-text search, fused with RRF."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Select, Text, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.embeddings import EmbeddingProvider
from app.models import Chunk, Source

RRF_K = 60
Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class RetrievalFilters:
    collections: list[str] = field(default_factory=list)
    source_ids: list[uuid.UUID] | None = None
    source_types: list[str] | None = None
    domains: list[str] | None = None
    exclude_flagged: bool = False


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    text: str
    score: float  # fused score (RRF) or raw similarity, depending on mode
    vector_similarity: float | None
    keyword_rank: int | None
    vector_rank: int | None
    title: str
    url: str | None
    domain: str | None
    source_type: str
    published_at: str | None
    retrieved_at: str
    security_flags: list[str]


class HybridRetriever:
    def __init__(self, embedder: EmbeddingProvider):
        self.embedder = embedder

    def _filtered(self, stmt: Select, f: RetrievalFilters) -> Select:
        stmt = stmt.join(Source, Source.id == Chunk.source_id)
        if f.collections:
            stmt = stmt.where(Chunk.collection.in_(f.collections))
        if f.source_ids:
            stmt = stmt.where(Chunk.source_id.in_(f.source_ids))
        if f.source_types:
            stmt = stmt.where(Source.source_type.in_(f.source_types))
        if f.domains:
            stmt = stmt.where(Source.domain.in_(f.domains))
        if f.exclude_flagged:
            stmt = stmt.where(func.jsonb_array_length(Source.security_flags) == 0)
        return stmt

    async def search(
        self,
        session: AsyncSession,
        query: str,
        filters: RetrievalFilters,
        k: int = 8,
        mode: Mode | None = None,
        query_vector: list[float] | None = None,
    ) -> list[RetrievedChunk]:
        mode = mode or get_settings().retrieval_mode
        n = max(k, get_settings().retrieval_candidates)
        vec_hits: list[tuple[uuid.UUID, float]] = []
        tiebreak: dict[uuid.UUID, str] = {}
        kw_hits: list[uuid.UUID] = []

        if mode in ("hybrid", "vector"):
            qv = query_vector or (await self.embedder.embed([query]))[0]
            # HNSW + WHERE filters: without iterative scans pgvector returns at most ef_search candidates
            # *before* filtering, silently losing results when other collections dominate the index.
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
            dist = Chunk.embedding.cosine_distance(qv)
            stmt = self._filtered(select(Chunk.id, Chunk.content_hash, (1 - dist).label("sim")), filters).order_by(dist).limit(n)
            rows = list(await session.execute(stmt))
            # Re-sort with a content-based tie-break so equal distances order deterministically.
            rows.sort(key=lambda r: (-round(float(r.sim), 9), r.content_hash))
            vec_hits = [(r.id, float(r.sim)) for r in rows]
            tiebreak.update({r.id: r.content_hash for r in rows})

        if mode in ("hybrid", "keyword"):
            tsq = func.websearch_to_tsquery("english", query)
            # OR the terms together: websearch_to_tsquery ANDs them, which is too strict for questions.
            or_q = func.to_tsquery(
                "english", func.replace(cast(func.plainto_tsquery("english", query), Text), " & ", " | ")
            )
            rank = func.ts_rank_cd(Chunk.tsv, or_q)
            stmt = (
                self._filtered(select(Chunk.id, Chunk.content_hash), filters)
                .where(Chunk.tsv.op("@@")(or_q))
                .order_by(rank.desc(), Chunk.tsv.op("@@")(tsq).desc(), Chunk.content_hash)
                .limit(n)
            )
            kw_rows = list(await session.execute(stmt))
            kw_hits = [r.id for r in kw_rows]
            tiebreak.update({r.id: r.content_hash for r in kw_rows})

        sims = dict(vec_hits)
        vrank = {cid: i for i, (cid, _) in enumerate(vec_hits)}
        krank = {cid: i for i, cid in enumerate(kw_hits)}
        fused: dict[uuid.UUID, float] = {}
        for cid in set(vrank) | set(krank):
            s = 0.0
            if cid in vrank:
                s += 1 / (RRF_K + vrank[cid] + 1)
            if cid in krank:
                s += 1 / (RRF_K + krank[cid] + 1)
            fused[cid] = s
        if mode == "vector":
            fused = {cid: sims[cid] for cid in vrank}
        # Deterministic tie-break: fused score, then best vector rank, then keyword rank.
        top = sorted(fused, key=lambda c: (-fused[c], vrank.get(c, 10**6), krank.get(c, 10**6), tiebreak.get(c, "")))[:k]
        if not top:
            return []

        rows = (
            await session.execute(
                select(Chunk, Source).join(Source, Source.id == Chunk.source_id).where(Chunk.id.in_(top))
            )
        ).all()
        by_id = {c.id: (c, s) for c, s in rows}
        out = []
        for cid in top:
            c, s = by_id[cid]
            out.append(
                RetrievedChunk(
                    chunk_id=c.id, source_id=s.id, text=c.text, score=fused[cid], vector_similarity=sims.get(cid),
                    keyword_rank=krank.get(cid), vector_rank=vrank.get(cid), title=s.title, url=s.url,
                    domain=s.domain, source_type=s.source_type, published_at=s.published_at,
                    retrieved_at=s.retrieved_at.isoformat(), security_flags=list(s.security_flags or []),
                )
            )
        return out

