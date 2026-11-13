import asyncio
import json

import httpx
import pytest

from app.main import create_app
from app.orchestration.runner import job_manager

pytestmark = pytest.mark.integration

Q = "Analyse the Freedonia electric vehicle market. Identify market size, major competitors and future outlook."


@pytest.fixture
async def client(db):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60) as c:
        yield c


async def _complete(client, depth="quick"):
    r = await client.post("/api/research", json={"question": Q, "depth": depth})
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    await asyncio.wait_for(asyncio.shield(job_manager._tasks[__import__("uuid").UUID(jid)]), 60)
    return jid


async def test_health_and_config(client):
    r = await client.get("/api/health")
    assert r.json()["database"] is True
    cfg = (await client.get("/api/config")).json()
    assert cfg["mock_mode"] is True and set(cfg["depths"]) == {"quick", "standard", "deep"}
    assert cfg["depths"]["deep"]["max_tool_calls"] > cfg["depths"]["quick"]["max_tool_calls"]


@pytest.mark.parametrize("body", [{"question": "short"}, {"question": "x" * 2001}, {"question": Q, "depth": "extreme"}])
async def test_create_validation(client, body):
    r = await client.post("/api/research", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


async def test_research_lifecycle_endpoints(client):
    jid = await _complete(client)
    detail = (await client.get(f"/api/research/{jid}")).json()
    assert detail["status"] == "completed" and detail["tasks"] and detail["counts"]["sources"] > 0
    assert (await client.get("/api/research")).json()[0]["id"] == jid

    sources = (await client.get(f"/api/research/{jid}/sources")).json()
    one = (await client.get(f"/api/research/{jid}/sources/{sources[0]['id']}")).json()
    assert one["text"] and one["url"]
    claims = (await client.get(f"/api/research/{jid}/claims")).json()
    assert claims and all(c["status"] != "pending" for c in claims)
    assert (await client.get(f"/api/research/{jid}/findings")).json()

    rep = (await client.get(f"/api/research/{jid}/report")).json()
    assert rep["versions"] == [1] and len(rep["sections"]) == 11

    md = await client.get(f"/api/research/{jid}/report/export?format=md")
    assert md.status_code == 200 and md.text.startswith("# Research Report") and "attachment" in md.headers["content-disposition"]
    html = await client.get(f"/api/research/{jid}/report/export?format=html")
    assert "<!DOCTYPE html>" in html.text and "<script" not in html.text

    fu = await client.post(f"/api/research/{jid}/followups", json={"question": "How many EVs were sold in fiscal year 2024?"})
    assert fu.status_code == 201 and fu.json()["citations"]
    assert len((await client.get(f"/api/research/{jid}/followups")).json()) == 1

    regen = await client.post(f"/api/research/{jid}/report/sections/opportunities/regenerate")
    assert regen.status_code == 200 and regen.json()["version"] == 2
    bad = await client.post(f"/api/research/{jid}/report/sections/references/regenerate")
    assert bad.status_code == 409

    # Cannot cancel a finished job; cannot resume a completed one.
    assert (await client.post(f"/api/research/{jid}/cancel")).status_code == 409
    assert (await client.post(f"/api/research/{jid}/resume")).status_code == 409

    usage = (await client.get("/api/usage")).json()
    assert usage["jobs"] == 1 and usage["tool_calls"] > 0

    assert (await client.delete(f"/api/research/{jid}")).status_code == 204
    assert (await client.get(f"/api/research/{jid}")).status_code == 404


async def test_sse_replays_and_ends(client):
    jid = await _complete(client)
    events = []
    async with client.stream("GET", f"/api/research/{jid}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        cur = {}
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                cur["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                cur["data"] = json.loads(line.split(":", 1)[1])
            elif line == "" and cur:
                events.append(cur)
                if cur.get("event") == "end":
                    break
                cur = {}
    types = [e["data"].get("type") for e in events if e.get("event") == "progress"]
    assert types[0] == "job_started" and "job_completed" in types
    assert events[-1]["event"] == "end" and events[-1]["data"]["status"] == "completed"
    # Resume from an event id: only later events are replayed.
    last_id = events[-2]["data"]["id"]
    async with client.stream("GET", f"/api/research/{jid}/events", headers={"Last-Event-ID": str(last_id)}) as r:
        body = (await r.aread()).decode()
    assert '"type": "job_completed"' not in body and "event: end" in body


async def test_not_found_and_bad_uuid(client):
    assert (await client.get("/api/research/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await client.get("/api/research/not-a-uuid")).status_code == 422


async def test_document_upload_security_and_rag(client):
    text = ("Freedonia's rail network carries 40 million passengers a year. The national rail operator "
            "plans to electrify 1,200 kilometres of track by 2029. ") * 5
    r = await client.post("/api/documents", files={"file": ("rail.txt", text.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["chunk_count"] >= 1
    # duplicate upload is deduplicated
    r2 = await client.post("/api/documents", files={"file": ("copy.txt", text.encode(), "text/plain")})
    assert r2.json()["id"] == doc["id"]

    assert (await client.post("/api/documents", files={"file": ("x.exe", b"MZ...", "application/octet-stream")})).status_code == 422
    assert (await client.post("/api/documents", files={"file": ("fake.pdf", b"<html>not pdf</html>", "application/pdf")})).status_code == 422
    assert (await client.post("/api/documents", files={"file": ("bin.txt", b"\x00\x01\x02" * 10, "text/plain")})).status_code == 422

    hits = (await client.post("/api/rag/search", json={"question": "rail electrification kilometres", "k": 3})).json()
    assert hits and hits[0]["source_id"] == doc["id"]
    ans = (await client.post("/api/rag/query", json={"question": "How many kilometres of track will be electrified?"})).json()
    assert "1,200" in ans["answer"] and ans["citations"]

    # Research job can use an uploaded document as evidence.
    r = await client.post("/api/research", json={"question": "Analyse Freedonia rail electrification plans and passenger numbers.",
                                                 "depth": "quick", "document_ids": [doc["id"]]})
    jid = r.json()["id"]
    await asyncio.wait_for(asyncio.shield(job_manager._tasks[__import__("uuid").UUID(jid)]), 60)
    srcs = (await client.get(f"/api/research/{jid}/sources")).json()
    assert any(s["source_type"] == "upload" for s in srcs)

    assert (await client.delete(f"/api/documents/{doc['id']}")).status_code == 204
    assert (await client.get("/api/documents")).json() == []


async def test_rate_limit(db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        codes = [(await c.post("/api/research", json={"question": "short"})).status_code for _ in range(3)]
    assert codes == [422, 422, 429]
