"""Per-job usage accounting and budget enforcement.

A `UsageTracker` is bound to the running research job through a ContextVar, so every LLM call,
embedding call and tool call made anywhere inside the job is counted against that job's limits
without threading the tracker through every function signature.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from app.core.config import get_settings
from app.core.errors import BudgetExceeded


@dataclass
class Limits:
    max_tokens: int | None = None  # prompt + completion tokens across all LLM calls
    max_cost_usd: float | None = None
    max_tool_calls: int | None = None  # searches + page fetches
    max_duration_s: float | None = None


@dataclass
class UsageTracker:
    limits: Limits = field(default_factory=Limits)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0
    llm_calls: int = 0
    llm_cache_hits: int = 0
    llm_failures: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    cost_usd: float = 0.0
    elapsed_offset_s: float = 0.0  # time already spent before a resume
    started_monotonic: float = field(default_factory=time.monotonic)
    # Attribution: everything recorded is also added to by_stage[stage] (plan, research, verify, …),
    # so the UI can show where the time and money of a job went. Set by the graph node wrapper.
    stage: str | None = None
    by_stage: dict[str, dict] = field(default_factory=dict)

    def _bucket(self) -> dict:
        b = self.by_stage.get(self.stage or "other")
        if b is None:
            b = self.by_stage[self.stage or "other"] = {
                "llm_calls": 0, "llm_cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "embedding_tokens": 0, "tool_calls": 0, "cost_usd": 0.0, "elapsed_s": 0.0}
        return b

    def add_stage_time(self, stage: str, seconds: float) -> None:
        prev, self.stage = self.stage, stage
        self._bucket()["elapsed_s"] += seconds
        self.stage = prev

    # ---- recording -------------------------------------------------------------------------
    def record_llm(self, prompt_tokens: int, completion_tokens: int, cached: bool = False) -> None:
        b = self._bucket()
        if cached:
            self.llm_cache_hits += 1
            b["llm_cache_hits"] += 1
            return
        cost = self.llm_cost(prompt_tokens, completion_tokens)
        self.llm_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd += cost
        b["llm_calls"] += 1
        b["prompt_tokens"] += prompt_tokens
        b["completion_tokens"] += completion_tokens
        b["cost_usd"] += cost

    @staticmethod
    def llm_cost(prompt_tokens: int, completion_tokens: int) -> float:
        s = get_settings()
        return (prompt_tokens * s.price_input_per_mtok + completion_tokens * s.price_output_per_mtok) / 1_000_000

    def record_embedding(self, tokens: int) -> None:
        cost = tokens * get_settings().price_embedding_per_mtok / 1_000_000
        self.embedding_tokens += tokens
        self.cost_usd += cost
        b = self._bucket()
        b["embedding_tokens"] += tokens
        b["cost_usd"] += cost

    def record_tool(self, kind: str, success: bool) -> None:
        self.tool_calls += 1
        self._bucket()["tool_calls"] += 1
        if kind == "search":
            self.search_calls += 1
        elif kind == "fetch":
            self.fetch_calls += 1
        if not success:
            self.tool_failures += 1

    # ---- enforcement -----------------------------------------------------------------------
    @property
    def elapsed_s(self) -> float:
        return self.elapsed_offset_s + (time.monotonic() - self.started_monotonic)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def exceeded(self) -> str | None:
        lim = self.limits
        if lim.max_duration_s is not None and self.elapsed_s >= lim.max_duration_s:
            return "max_duration"
        if lim.max_tokens is not None and self.total_tokens >= lim.max_tokens:
            return "max_tokens"
        if lim.max_cost_usd is not None and self.cost_usd >= lim.max_cost_usd:
            return "max_cost"
        if lim.max_tool_calls is not None and self.tool_calls >= lim.max_tool_calls:
            return "max_tool_calls"
        return None

    def check(self) -> None:
        if reason := self.exceeded():
            raise BudgetExceeded(reason)

    def remaining_fraction(self) -> float:
        """Smallest remaining share across all limits (1.0 = untouched, 0 = exhausted)."""
        lim, fracs = self.limits, [1.0]
        if lim.max_tokens:
            fracs.append(1 - self.total_tokens / lim.max_tokens)
        if lim.max_cost_usd:
            fracs.append(1 - self.cost_usd / lim.max_cost_usd)
        if lim.max_tool_calls:
            fracs.append(1 - self.tool_calls / lim.max_tool_calls)
        if lim.max_duration_s:
            fracs.append(1 - self.elapsed_s / lim.max_duration_s)
        return max(0.0, min(fracs))

    def snapshot(self) -> dict:
        d = asdict(self)
        d.pop("limits"), d.pop("started_monotonic"), d.pop("elapsed_offset_s"), d.pop("stage")
        d["by_stage"] = {k: {**v, "cost_usd": round(v["cost_usd"], 6), "elapsed_s": round(v["elapsed_s"], 2)}
                         for k, v in self.by_stage.items()}
        d["elapsed_s"] = round(self.elapsed_s, 2)
        d["total_tokens"] = self.total_tokens
        d["cost_usd"] = round(self.cost_usd, 6)
        return d

    @classmethod
    def restore(cls, snapshot: dict | None, limits: Limits) -> UsageTracker:
        t = cls(limits=limits)
        if snapshot:
            for k in (
                "prompt_tokens", "completion_tokens", "embedding_tokens", "llm_calls", "llm_cache_hits",
                "llm_failures", "tool_calls", "tool_failures", "search_calls", "fetch_calls", "cost_usd",
            ):
                if k in snapshot:
                    setattr(t, k, snapshot[k])
            t.elapsed_offset_s = float(snapshot.get("elapsed_s", 0.0))
            t.by_stage = {k: dict(v) for k, v in (snapshot.get("by_stage") or {}).items()}
        return t


_current: ContextVar[UsageTracker | None] = ContextVar("scout_usage", default=None)


def current_usage() -> UsageTracker:
    """Tracker for the active job, or a throwaway unlimited tracker outside of a job."""
    t = _current.get()
    if t is None:
        t = UsageTracker()
        _current.set(t)
    return t


def bind_usage(tracker: UsageTracker):
    return _current.set(tracker)


def unbind_usage(token) -> None:
    _current.reset(token)
