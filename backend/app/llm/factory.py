"""Builds providers from settings. Mock providers are used only when explicitly configured."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.llm.embeddings import EmbeddingProvider, HashingEmbeddings, OpenAIEmbeddings
from app.llm.gateway import LLMGateway


@lru_cache
def get_llm() -> LLMGateway:
    s = get_settings()
    if s.llm_provider == "mock":
        from app.llm.mock_provider import MockProvider

        return LLMGateway(MockProvider(), s)
    from app.llm.openai_provider import OpenAIProvider

    return LLMGateway(OpenAIProvider(s), s)


@lru_cache
def get_embedder() -> EmbeddingProvider:
    s = get_settings()
    if s.embedding_provider == "mock":
        return HashingEmbeddings(s.embedding_dim)
    return OpenAIEmbeddings(s)
