"""Progress events: persisted (for replay / inspection) and pushed to in-process SSE listeners.

Events are concise execution summaries (stage changes, tool calls, findings, verification
verdicts). They never contain model reasoning or prompts.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import select

from app.database.session import session_scope
from app.models import ResearchEvent


class EventBus:
    def __init__(self) -> None:
        self._conds: dict[uuid.UUID, asyncio.Condition] = defaultdict(asyncio.Condition)

    async def emit(self, job_id: uuid.UUID, type: str, message: str, stage: str | None = None, data: dict | None = None) -> int:
        async with session_scope() as s:
            ev = ResearchEvent(job_id=job_id, type=type, stage=stage, message=message[:2000], data=data or {})
            s.add(ev)
            await s.flush()
            ev_id = ev.id
        cond = self._conds[job_id]
        async with cond:
            cond.notify_all()
        return ev_id

    async def wait(self, job_id: uuid.UUID, timeout: float) -> None:
        """Wait for a new event for job_id (or timeout; callers then re-poll the DB, which also
        makes streaming work when events are written by another process)."""
        cond = self._conds[job_id]
        async with cond:
            try:
                await asyncio.wait_for(cond.wait(), timeout)
            except TimeoutError:
                pass

    async def since(self, job_id: uuid.UUID, after_id: int, limit: int = 500) -> list[ResearchEvent]:
        async with session_scope() as s:
            rows = await s.execute(
                select(ResearchEvent)
                .where(ResearchEvent.job_id == job_id, ResearchEvent.id > after_id)
                .order_by(ResearchEvent.id)
                .limit(limit)
            )
            return list(rows.scalars())


event_bus = EventBus()
