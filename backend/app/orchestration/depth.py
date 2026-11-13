"""Research depth profiles. Each one sets real execution limits and breadth, not just a label."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.llm.usage import Limits

Depth = Literal["quick", "standard", "deep"]


@dataclass(frozen=True)
class DepthConfig:
    name: str
    max_tasks: int  # tasks in the initial plan
    queries_per_task: int
    results_per_query: int
    max_iterations: int  # total research rounds (1 = no gap-filling loop)
    max_gap_tasks: int  # new tasks per gap-filling round
    findings_per_task: int
    passages_per_task: int  # chunks given to the extraction agent per task
    max_claims: int  # claims that undergo verification
    verify_passages: int  # evidence passages retrieved per claim
    concurrency: int
    task_retries: int
    # budget
    max_tool_calls: int
    max_duration_s: int
    max_tokens: int
    max_cost_usd: float

    def limits(self) -> Limits:
        return Limits(self.max_tokens, self.max_cost_usd, self.max_tool_calls, self.max_duration_s)

    def as_dict(self) -> dict:
        return asdict(self)


DEPTHS: dict[str, DepthConfig] = {
    "quick": DepthConfig("quick", 4, 1, 3, 1, 0, 6, 8, 8, 4, 4, 1, 30, 300, 150_000, 0.50),
    "standard": DepthConfig("standard", 6, 2, 4, 2, 3, 8, 10, 15, 6, 4, 2, 90, 900, 450_000, 1.50),
    "deep": DepthConfig("deep", 9, 3, 5, 3, 4, 10, 12, 25, 8, 5, 2, 220, 1800, 1_200_000, 5.00),
}


def get_depth(name: str) -> DepthConfig:
    return DEPTHS[name]


def from_dict(d: dict) -> DepthConfig:
    return DepthConfig(**d)
