"""Provider-independent web search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.core.config import Settings, get_settings
from app.core.errors import ProviderError
from app.core.text import idf_weights, weighted_overlap


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    score: float = 0.0
    published_at: str | None = None
    provider: str = ""
    extra: dict = field(default_factory=dict)


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class _Retryable(Exception):
    pass


class TavilySearch:
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.tavily_api_key:
            raise ProviderError("TAVILY_API_KEY is not configured")
        self._key = settings.tavily_api_key.get_secret_value()
        self._retries = settings.llm_max_retries
        self._transport = transport

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {"query": query[:400], "max_results": max_results, "search_depth": "advanced", "include_answer": False}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((_Retryable, httpx.TransportError)),
                    stop=stop_after_attempt(self._retries), wait=wait_exponential_jitter(1, 10), reraise=True,
                ):
                    with attempt:
                        resp = await client.post(self.endpoint, json=payload, headers=headers)
                        if resp.status_code == 429 or resp.status_code >= 500:
                            raise _Retryable(f"HTTP {resp.status_code}")
        except (_Retryable, httpx.HTTPError) as e:
            raise ProviderError(f"Search failed: {e}") from e
        if resp.status_code >= 400:
            raise ProviderError(f"Search rejected (HTTP {resp.status_code})")
        data = resp.json()
        return [
            SearchResult(
                url=r["url"], title=r.get("title") or r["url"], snippet=r.get("content") or "",
                score=float(r.get("score") or 0.0), published_at=r.get("published_date"), provider=self.name,
            )
            for r in data.get("results", [])
            if r.get("url")
        ]


class MockSearch:
    """Lexical search over the bundled fixture corpus (SEARCH_PROVIDER=mock only)."""

    name = "mock"

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        from app.retrieval.fetcher import load_fixture_corpus

        docs = load_fixture_corpus()
        idf = idf_weights([d["title"] + " " + d["content"] for d in docs])
        scored = []
        for d in docs:
            sc = 0.7 * weighted_overlap(query, d["title"] + " " + d["content"], idf) + 0.3 * weighted_overlap(query, d["title"], idf)
            if sc > 0.3:
                scored.append((sc, d))
        scored.sort(key=lambda x: (-x[0], x[1]["url"]))
        return [
            SearchResult(url=d["url"], title=d["title"], snippet=d["content"][:200], score=round(sc, 3),
                         published_at=d.get("published"), provider=self.name)
            for sc, d in scored[:max_results]
        ]


def get_search_provider() -> SearchProvider:
    s = get_settings()
    return MockSearch() if s.search_provider == "mock" else TavilySearch(s)
