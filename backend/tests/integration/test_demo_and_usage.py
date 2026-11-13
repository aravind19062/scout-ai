"""Public demo guardrails, usage attribution per agent stage, and reranked retrieval."""

import asyncio
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.main import create_app
from app.orchestration.runner import job_manager

pytestmark = pytest.mark.integration

Q = "Analyse the Freedonia electric vehicle market. Identify market size, major competitors and future outlook."


@pytest.fixture
async def client(db):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60) as c:
        yield c


@pytest.fixture
def demo(monkeypatch):
    s = get_settings()
    for k, v in {"demo_mode": True, "demo_max_depth": "quick", "demo_jobs_per_client_per_day": 2,
                 "demo_daily_cost_cap_usd": 100.0, "demo_allow_uploads": False}.items():
        monkeypatch.setattr(s, k, v)
    return s


async def _wait(jid: str):
    t = job_manager._tasks.get(uuid.UUID(jid))
    if t:
        await asyncio.wait_for(asyncio.shield(t), 60)


async def test_demo_caps_depth_quota_and_features(client, demo):
    cfg = (await client.get("/api/config")).json()["demo"]
    assert cfg["enabled"] and cfg["max_depth"] == "quick" and cfg["jobs_used_today"] == 0

    r = await client.post("/api/research", json={"question": Q, "depth": "deep"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "demo_disabled"

    ids = []
    for _ in range(2):
        r = await client.post("/api/research", json={"question": Q, "depth": "quick"})
        assert r.status_code == 202, r.text
        ids.append(r.json()["id"])
    r = await client.post("/api/research", json={"question": Q, "depth": "quick"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "demo_limit"
    assert (await client.get("/api/config")).json()["demo"]["jobs_used_today"] == 2
    # Visitors only see their own sessions in the public demo.
    assert {j["id"] for j in (await client.get("/api/research")).json()} == set(ids)
    other = httpx.AsyncClient(transport=httpx.ASGITransport(app=client._transport.app, client=("203.0.113.9", 1234)),
                              base_url="http://test")
    async with other:
        assert (await other.get("/api/research")).json() == []

    for jid in ids:
        await _wait(jid)
    r = await client.post(f"/api/research/{ids[0]}/deepen", json={"instructions": "look at battery supply chains"})
    assert r.status_code == 403
    r = await client.post("/api/documents", files={"file": ("a.txt", b"hello world " * 50, "text/plain")})
    assert r.status_code == 403


async def test_demo_daily_cost_cap_reserves_in_flight_budget(client, demo, monkeypatch):
    # Cap below one quick job's worst-case budget: refused before any money is spent.
    from app.orchestration.depth import get_depth

    monkeypatch.setattr(demo, "demo_daily_cost_cap_usd", get_depth("quick").max_cost_usd * 0.5)
    r = await client.post("/api/research", json={"question": Q, "depth": "quick"})
    assert r.status_code == 429 and "spending limit" in r.json()["error"]["message"]


async def test_usage_is_attributed_to_agent_stages(client):
    r = await client.post("/api/research", json={"question": Q, "depth": "quick"})
    jid = r.json()["id"]
    await _wait(jid)
    usage = (await client.get(f"/api/research/{jid}")).json()["usage"]
    stages = usage["by_stage"]
    assert {"plan", "research", "analyze", "verify", "report"} <= set(stages)
    assert stages["research"]["tool_calls"] == usage["tool_calls"]
    assert sum(b["llm_calls"] for b in stages.values()) == usage["llm_calls"]
    assert all(b["elapsed_s"] >= 0 for b in stages.values())

    agg = (await client.get("/api/usage")).json()
    assert agg["per_completed_job"]["avg_cost_usd"] is not None
    assert set(agg["by_stage"]) >= {"plan", "research", "report"}
    assert 0 <= agg["claims"]["supported_rate"] <= 1


async def test_retrieval_with_reranker(db, monkeypatch):
    from app.database.session import session_scope
    from app.llm.embeddings import HashingEmbeddings
    from app.rag.ingestion import IngestionService
    from app.rag.retriever import HybridRetriever, RetrievalFilters
    from app.retrieval.fetcher import load_fixture_corpus

    emb = HashingEmbeddings(1536)
    svc = IngestionService(emb)
    async with session_scope() as s:
        for d in load_fixture_corpus():
            await svc.ingest(s, collection="rr", text=d["content"], title=d["title"], source_type="web", url=d["url"])
    retr, f = HybridRetriever(emb), RetrievalFilters(collections=["rr"])
    async with session_scope() as s:
        plain = await retr.search(s, "battery manufacturing investment", f, k=5)
        monkeypatch.setattr(get_settings(), "reranker", "lexical")
        reranked = await retr.search(s, "battery manufacturing investment", f, k=5)
        forced_off = await retr.search(s, "battery manufacturing investment", f, k=5, reranker="none")
    assert len(reranked) == 5 and all(h.rerank_score is not None for h in reranked)
    assert reranked == sorted(reranked, key=lambda h: -h.rerank_score)
    assert all(h.rerank_score is None for h in plain + forced_off)
    assert [h.chunk_id for h in forced_off] == [h.chunk_id for h in plain]
