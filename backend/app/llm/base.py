"""Provider-independent LLM interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMRequest:
    task: str  # logical task name, e.g. "plan", "judge_evidence"; used for caching/metrics/mocking
    system: str
    user: str
    model: str
    inputs: dict[str, Any]  # structured inputs the prompt was rendered from (used by the mock provider)
    max_output_tokens: int = 4000


@dataclass
class LLMResponse:
    parsed: Any
    prompt_tokens: int
    completion_tokens: int
    model: str


class LLMProvider(Protocol):
    name: str

    async def structured(self, req: LLMRequest, schema: type[T]) -> LLMResponse: ...
