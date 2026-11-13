"""Research job state. The database is the system of record for evidence and progress;
LangGraph state only carries control-flow data (see docs/AGENT_WORKFLOW.md)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    depth: Mapped[str] = mapped_column(String(20))
    # queued | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # plan | research | analyze | verify | gap_plan | report | done
    stage: Mapped[str] = mapped_column(String(20), default="plan")
    limits: Mapped[dict] = mapped_column(JSONType, default=dict)
    usage: Mapped[dict] = mapped_column(JSONType, default=dict)
    analysis: Mapped[dict] = mapped_column(JSONType, default=dict)
    halt_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_mode: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tasks: Mapped[list[ResearchTask]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ResearchTask.created_at"
    )


class ResearchTask(Base):
    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(20))  # human-readable id, e.g. "T3"
    objective: Mapped[str] = mapped_column(Text)
    queries: Mapped[list] = mapped_column(JSONType, default=list)
    expected_evidence: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 = highest
    # pending | running | completed | failed | skipped
    status: Mapped[str] = mapped_column(String(20), default="pending")
    origin: Mapped[str] = mapped_column(String(20), default="plan")  # plan | gap | followup
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[ResearchJob] = relationship(back_populates="tasks")


class Finding(Base):
    """An atomic piece of evidence extracted from a source chunk, with a verbatim quote."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="fact")  # fact | interpretation | forecast
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    """An important claim proposed by the analysis stage, subject to evidence-based verification."""

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text, index=True)
    category: Mapped[str] = mapped_column(String(60), default="general")
    importance: Mapped[int] = mapped_column(Integer, default=2)  # 1 = critical
    kind: Mapped[str] = mapped_column(String(20), default="fact")
    # pending | supported | partially_supported | conflicting | unsupported | unable_to_verify
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_count: Mapped[int] = mapped_column(Integer, default=0)
    contradict_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_domains: Mapped[int] = mapped_column(Integer, default=0)
    finding_ids: Mapped[list] = mapped_column(JSONType, default=list)
    verify_attempts: Mapped[int] = mapped_column(Integer, default=0)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    evidence: Mapped[list[ClaimEvidence]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    stance: Mapped[str] = mapped_column(String(20))  # supports | partial | contradicts | irrelevant
    quote: Mapped[str] = mapped_column(Text, default="")
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    numbers_consistent: Mapped[bool] = mapped_column(Boolean, default=True)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)

    claim: Mapped[Claim] = relationship(back_populates="evidence")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text)
    sections: Mapped[list] = mapped_column(JSONType, default=list)  # [{key, title, markdown}]
    references: Mapped[list] = mapped_column(JSONType, default=list)  # [{label, source_id, url, title, ...}]
    citation_stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FollowUp(Base):
    __tablename__ = "followups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONType, default=list)
    insufficient_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchEvent(Base):
    """Append-only progress log; backs SSE streaming and replay (Last-Event-ID)."""

    __tablename__ = "research_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCacheEntry(Base):
    """Content-addressed cache of LLM responses so resumed/re-run stages don't pay twice."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSONType)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
