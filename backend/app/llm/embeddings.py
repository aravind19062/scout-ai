"""Embedding providers."""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Protocol

import tiktoken
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.core.config import Settings
from app.core.errors import ProviderError
from app.core.text import content_words
from app.llm.usage import current_usage


@lru_cache
def _encoding():
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


class EmbeddingProvider(Protocol):
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddings:
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        self.batch = settings.embedding_batch_size
        self._retries = settings.llm_max_retries
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_s, max_retries=0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            batch = texts[i : i + self.batch]
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)),
                    stop=stop_after_attempt(self._retries), wait=wait_exponential_jitter(1, 20), reraise=True,
                ):
                    with attempt:
                        resp = await self._client.embeddings.create(model=self.model, input=batch, dimensions=self.dim)
            except Exception as e:
                raise ProviderError(f"Embedding request failed: {type(e).__name__}") from e
            current_usage().record_embedding(resp.usage.total_tokens if resp.usage else 0)
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return out


class HashingEmbeddings:
    """Deterministic offline embeddings (feature hashing of unigrams + bigrams, L2-normalised).

    Captures lexical similarity only. Used in mock mode and tests; never used unless configured.
    """

    model = "mock-hashing-v1"

    def __init__(self, dim: int):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        words = content_words(text)
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        current_usage().record_embedding(sum(count_tokens(t) for t in texts))
        return [self._vec(t) for t in texts]
