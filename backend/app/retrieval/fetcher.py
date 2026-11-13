"""Safe HTTP fetching of web pages and PDFs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import FetchError, UnsafeURLError
from app.retrieval.url_safety import assert_public_url

ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "application/pdf", "text/plain")


@dataclass
class FetchedDocument:
    url: str  # final URL after redirects
    content_type: str
    body: bytes
    status: int


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedDocument: ...


class HttpFetcher:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings or get_settings()
        self._transport = transport

    async def fetch(self, url: str) -> FetchedDocument:
        s = self.s
        headers = {"User-Agent": s.fetch_user_agent, "Accept": "text/html,application/pdf;q=0.9,text/plain;q=0.8"}
        timeout = httpx.Timeout(s.fetch_timeout_s, connect=min(5.0, s.fetch_timeout_s))
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout, headers=headers, transport=self._transport
        ) as client:
            current = url
            for _hop in range(s.fetch_max_redirects + 1):
                await assert_public_url(current, s.fetch_allow_private_networks)
                try:
                    async with client.stream("GET", current) as resp:
                        if resp.is_redirect:
                            loc = resp.headers.get("location")
                            if not loc:
                                raise FetchError("Redirect without location")
                            current = urljoin(current, loc)
                            continue
                        if resp.status_code >= 400:
                            raise FetchError(f"HTTP {resp.status_code}")
                        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        if ctype and not ctype.startswith(ALLOWED_TYPES):
                            raise FetchError(f"Unsupported content type: {ctype}")
                        declared = int(resp.headers.get("content-length") or 0)
                        if declared > s.fetch_max_bytes:
                            raise FetchError("Response too large")
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > s.fetch_max_bytes:
                                raise FetchError("Response too large")
                            chunks.append(chunk)
                        return FetchedDocument(str(resp.url), ctype or "text/html", b"".join(chunks), resp.status_code)
                except httpx.TimeoutException as e:
                    raise FetchError("Timed out") from e
                except httpx.HTTPError as e:
                    raise FetchError(f"Network error: {type(e).__name__}") from e
            raise FetchError("Too many redirects")


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "corpus.json"


@lru_cache
def load_fixture_corpus() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())["documents"]


class FixtureFetcher:
    """Serves the bundled fixture corpus. Used only with SEARCH_PROVIDER=mock."""

    async def fetch(self, url: str) -> FetchedDocument:
        for d in load_fixture_corpus():
            if d["url"] == url:
                if d.get("unreachable"):
                    raise FetchError("HTTP 503 (fixture marked unreachable)")
                html = f"<html><head><title>{d['title']}</title>"
                if d.get("published"):
                    html += f'<meta property="article:published_time" content="{d["published"]}">'
                html += "</head><body><article>" + "".join(f"<p>{p}</p>" for p in d["content"].split("\n\n")) + "</article></body></html>"
                return FetchedDocument(url, "text/html", html.encode(), 200)
        raise FetchError("HTTP 404")


def get_fetcher() -> Fetcher:
    if get_settings().search_provider == "mock":
        return FixtureFetcher()
    return HttpFetcher()


__all__ = ["FetchedDocument", "Fetcher", "FixtureFetcher", "HttpFetcher", "UnsafeURLError", "get_fetcher"]
