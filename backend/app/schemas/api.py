"""Request/response models for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Depth = Literal["quick", "standard", "deep"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateResearch(BaseModel):
    question: str = Field(min_length=10, max_length=2000)
    depth: Depth = "standard"
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 10:
            raise ValueError("question is too short")
        return v


class DeepenRequest(BaseModel):
    instructions: str = Field(min_length=3, max_length=500)


class FollowUpRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class TaskOut(ORM):
    id: uuid.UUID
    key: str
    objective: str
    queries: list[str]
    expected_evidence: str
    priority: int
    status: str
    origin: str
    iteration: int
    attempts: int
    summary: str | None
    error: str | None


class JobSummary(ORM):
    id: uuid.UUID
    question: str
    depth: str
    status: str
    stage: str
    iteration: int
    halt_reason: str | None
    error: str | None
    usage: dict
    provider_mode: dict
    created_at: datetime
    completed_at: datetime | None


class JobDetail(JobSummary):
    limits: dict
    analysis: dict
    started_at: datetime | None
    tasks: list[TaskOut]
    counts: dict


class SourceOut(ORM):
    id: uuid.UUID
    url: str | None
    title: str
    domain: str | None
    source_type: str
    published_at: str | None
    retrieved_at: datetime
    char_count: int
    extraction_method: str
    search_query: str | None
    security_flags: list[str]
    task_id: uuid.UUID | None


class SourceWithText(SourceOut):
    text: str


class FindingOut(ORM):
    id: uuid.UUID
    task_id: uuid.UUID | None
    source_id: uuid.UUID
    statement: str
    evidence_quote: str
    kind: str
    quote_verified: bool
    created_at: datetime


class EvidenceOut(ORM):
    source_id: uuid.UUID
    chunk_id: uuid.UUID
    stance: str
    quote: str
    quote_verified: bool
    numbers_consistent: bool
    relevance: float


class ClaimOut(ORM):
    id: uuid.UUID
    statement: str
    category: str
    importance: int
    kind: str
    status: str
    rationale: str | None
    support_count: int
    contradict_count: int
    distinct_domains: int
    iteration: int
    evidence: list[EvidenceOut]


class ReportOut(ORM):
    id: uuid.UUID
    job_id: uuid.UUID
    version: int
    title: str
    sections: list[dict]
    references: list[dict]
    citation_stats: dict
    created_at: datetime
    versions: list[int] = []


class FollowUpOut(ORM):
    id: uuid.UUID
    question: str
    answer: str
    citations: list[dict]
    insufficient_evidence: bool
    created_at: datetime


class DocumentOut(ORM):
    id: uuid.UUID
    title: str
    source_type: str
    char_count: int
    extraction_method: str
    retrieved_at: datetime
    security_flags: list[str]
    chunk_count: int = 0


class RagQuery(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    document_ids: list[uuid.UUID] | None = None
    job_id: uuid.UUID | None = None
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"
    k: int = Field(default=8, ge=1, le=30)


class RagSearch(RagQuery):
    source_types: list[str] | None = None
    domains: list[str] | None = None


class ChunkHit(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    title: str
    url: str | None
    domain: str | None
    score: float
    vector_similarity: float | None
    keyword_rank: int | None
    text: str
