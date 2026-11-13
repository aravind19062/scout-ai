"""Per-job execution context: providers, limits, usage, cancellation checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.errors import JobCancelled
from app.database.session import session_scope
from app.llm.embeddings import EmbeddingProvider
from app.llm.gateway import LLMGateway
from app.llm.usage import UsageTracker
from app.models import ResearchJob
from app.orchestration.depth import DepthConfig
from app.orchestration.events import EventBus
from app.rag.ingestion import IngestionService
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.retrieval.fetcher import Fetcher
from app.retrieval.search import SearchProvider


@dataclass
class JobContext:
    job_id: uuid.UUID
    question: str
    depth: DepthConfig
    usage: UsageTracker
    llm: LLMGateway
    embedder: EmbeddingProvider
    search: SearchProvider
    fetcher: Fetcher
    events: EventBus

    @property
    def collection(self) -> str:
        return f"job:{self.job_id}"

    @property
    def retriever(self) -> HybridRetriever:
        return HybridRetriever(self.embedder)

    @property
    def ingestion(self) -> IngestionService:
        return IngestionService(self.embedder)

    def filters(self, **kw) -> RetrievalFilters:
        return RetrievalFilters(collections=[self.collection], **kw)

    async def emit(self, type: str, message: str, stage: str | None = None, **data) -> None:
        await self.events.emit(self.job_id, type, message, stage, data)

    async def check_cancelled(self) -> None:
        async with session_scope() as s:
            flag = await s.scalar(select(ResearchJob.cancel_requested).where(ResearchJob.id == self.job_id))
        if flag:
            raise JobCancelled("Research was cancelled by the user")

    async def checkpoint(self) -> None:
        """Called between units of work: honours cancellation, then enforces budgets."""
        await self.check_cancelled()
        self.usage.check()
