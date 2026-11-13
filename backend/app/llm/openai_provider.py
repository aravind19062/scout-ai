from __future__ import annotations

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.core.config import Settings
from app.core.errors import ProviderError
from app.llm.base import LLMRequest, LLMResponse, T

RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_s,
            max_retries=0,  # retries handled below so they are visible and bounded
        )
        self._max_retries = settings.llm_max_retries

    async def structured(self, req: LLMRequest, schema: type[T]) -> LLMResponse:
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(RETRYABLE),
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential_jitter(initial=1, max=20),
                reraise=True,
            ):
                with attempt:
                    completion = await self._client.chat.completions.parse(
                        model=req.model,
                        messages=[
                            {"role": "system", "content": req.system},
                            {"role": "user", "content": req.user},
                        ],
                        response_format=schema,
                        max_completion_tokens=req.max_output_tokens,
                    )
        except RETRYABLE as e:
            raise ProviderError(f"LLM call '{req.task}' failed after retries: {type(e).__name__}") from e
        except Exception as e:  # auth errors, bad requests: not retryable
            raise ProviderError(f"LLM call '{req.task}' failed: {type(e).__name__}: {e}") from e

        msg = completion.choices[0].message
        if getattr(msg, "refusal", None):
            raise ProviderError(f"LLM refused task '{req.task}': {msg.refusal}")
        if msg.parsed is None:
            raise ProviderError(f"LLM returned no parseable output for task '{req.task}'")
        usage = completion.usage
        return LLMResponse(
            parsed=msg.parsed,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            model=completion.model,
        )
