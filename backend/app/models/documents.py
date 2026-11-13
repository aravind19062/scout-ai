"""Sources (web pages, PDFs, uploads) and their embedded chunks."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.database.base import Base, utcnow

EMBED_DIM = get_settings().embedding_dim
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Source(Base):
    """A retrieved or uploaded document. `collection` scopes retrieval (e.g. "job:<id>", "library")."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("collection", "content_hash", name="uq_source_collection_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    collection: Mapped[str] = mapped_column(String(120), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True, nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20))  # web | pdf | upload
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)  # as reported by the page
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    extraction_method: Mapped[str] = mapped_column(String(40), default="")
    search_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_flags: Mapped[list] = mapped_column(JSONType, default=list)
    text: Mapped[str] = mapped_column(Text, default="")

    chunks: Mapped[list[Chunk]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_collection_hash", "collection", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    collection: Mapped[str] = mapped_column(String(120), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str] = mapped_column(String(80))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    source: Mapped[Source] = relationship(back_populates="chunks")
