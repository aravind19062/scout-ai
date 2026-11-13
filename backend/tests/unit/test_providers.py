import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.errors import FetchError, ProviderError, UnsafeURLError
from app.retrieval.fetcher import FixtureFetcher, HttpFetcher
from app.retrieval.search import MockSearch, TavilySearch


async def test_mock_search_is_deterministic_and_relevant():
    r1 = await MockSearch().search("Freedonia battery technology developments", 3)
    r2 = await MockSearch().search("Freedonia battery technology developments", 3)
    assert [r.url for r in r1] == [r.url for r in r2]
    assert "batteryjournal" in r1[0].url


async def test_fixture_fetcher_unreachable_and_missing():
    with pytest.raises(FetchError):
        await FixtureFetcher().fetch("https://paywalled.example/freedonia-ev-deep-dive")
    with pytest.raises(FetchError):
        await FixtureFetcher().fetch("https://nope.example/")


async def test_tavily_parses_results_and_retries_on_429():
    calls = {"n": 0}

    def handler(req: httpx.Request):
        calls["n"] += 1
        assert req.headers["authorization"] == "Bearer tvly-test"
        if calls["n"] == 1:
            return httpx.Response(429)
        body = json.loads(req.content)
        assert body["max_results"] == 2
        return httpx.Response(200, json={"results": [{"url": "https://a.example/x", "title": "A", "content": "snip", "score": 0.9}]})

    s = get_settings().model_copy(update={"tavily_api_key": SecretStr("tvly-test")})
    res = await TavilySearch(s, transport=httpx.MockTransport(handler)).search("q", 2)
    assert calls["n"] == 2 and res[0].url == "https://a.example/x" and res[0].provider == "tavily"


async def test_tavily_auth_error_is_provider_error():
    s = get_settings().model_copy(update={"tavily_api_key": SecretStr("tvly-bad")})
    with pytest.raises(ProviderError):
        await TavilySearch(s, transport=httpx.MockTransport(lambda r: httpx.Response(401))).search("q")


async def test_http_fetcher_blocks_redirect_to_private_network():
    def handler(req: httpx.Request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(UnsafeURLError):
        await HttpFetcher(transport=httpx.MockTransport(handler)).fetch("https://1.1.1.1/start")


async def test_http_fetcher_enforces_size_and_type():
    s = get_settings().model_copy(update={"fetch_max_bytes": 100})
    big = httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 500))
    with pytest.raises(FetchError, match="too large"):
        await HttpFetcher(s, transport=big).fetch("https://1.1.1.1/")
    exe = httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"MZ"))
    with pytest.raises(FetchError, match="Unsupported"):
        await HttpFetcher(transport=exe).fetch("https://1.1.1.1/")


async def test_http_fetcher_follows_safe_redirect():
    def handler(req: httpx.Request):
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<p>ok</p>")

    doc = await HttpFetcher(transport=httpx.MockTransport(handler)).fetch("https://1.1.1.1/a")
    assert doc.url.endswith("/b") and doc.content_type == "text/html"
