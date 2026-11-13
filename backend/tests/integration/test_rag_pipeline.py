import pytest
from sqlalchemy import func, select

from app.database.session import session_scope
from app.llm.embeddings import HashingEmbeddings
from app.llm.factory import get_llm
from app.models import Chunk, Source
from app.rag.ingestion import IngestionService
from app.rag.qa import grounded_answer
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.retrieval.fetcher import load_fixture_corpus

pytestmark = pytest.mark.integration


async def _ingest_corpus(collection="library"):
    emb = HashingEmbeddings(1536)
    svc = IngestionService(emb)
    async with session_scope() as s:
        results = [
            await svc.ingest(s, collection=collection, text=d["content"], title=d["title"], source_type="web",
                             url=d["url"], published_at=d.get("published"))
            for d in load_fixture_corpus()
        ]
    return emb, results


async def test_ingestion_dedupes_documents_and_reuses_embeddings(db):
    _, results = await _ingest_corpus()
    created = [r for r in results if r.created]
    # duplicate-of-ministry has identical text -> not stored twice
    assert len(created) == len(results) - 1
    flagged = [r.source for r in created if r.source.security_flags]
    assert any("evforum" in (s.url or "") for s in flagged)

    # Ingesting the same corpus into another collection reuses every embedding.
    svc = IngestionService(HashingEmbeddings(1536))
    d = load_fixture_corpus()[0]
    async with session_scope() as s:
        r = await svc.ingest(s, collection="other", text=d["content"], title="x", source_type="web", url=d["url"])
    assert r.created and r.embeddings_reused == r.chunks_created > 0

    async with session_scope() as s:
        n_sources = await s.scalar(select(func.count()).select_from(Source))
        n_chunks = await s.scalar(select(func.count()).select_from(Chunk))
    assert n_sources == len(created) + 1 and n_chunks >= n_sources


@pytest.mark.parametrize("mode", ["hybrid", "vector", "keyword"])
async def test_retrieval_modes_find_relevant_chunk(db, mode):
    emb, _ = await _ingest_corpus()
    r = HybridRetriever(emb)
    async with session_scope() as s:
        hits = await r.search(s, "battery pack prices per kilowatt-hour", RetrievalFilters(collections=["library"]), k=3, mode=mode)
    assert hits, mode
    assert "batteryjournal" in hits[0].url


async def test_metadata_filters(db):
    emb, _ = await _ingest_corpus()
    r = HybridRetriever(emb)
    async with session_scope() as s:
        hits = await r.search(s, "electric vehicle market size", RetrievalFilters(collections=["library"], domains=["finpost.example"]), k=5)
        assert hits and all(h.domain == "finpost.example" for h in hits)
        none = await r.search(s, "electric vehicle", RetrievalFilters(collections=["does-not-exist"]), k=5)
        assert none == []
        clean = await r.search(s, "forum best EV Sunrise Electric", RetrievalFilters(collections=["library"], exclude_flagged=True), k=10)
        assert all("evforum" not in (h.url or "") for h in clean)


async def test_grounded_answer_cites_real_sources_or_admits_insufficiency(db):
    emb, _ = await _ingest_corpus()
    r = HybridRetriever(emb)
    async with session_scope() as s:
        res = await grounded_answer(s, get_llm(), r, "What were battery pack prices in 2024?", RetrievalFilters(collections=["library"]))
        assert not res.insufficient_evidence
        assert res.citations and all(c.url for c in res.citations)
        assert "118" in res.answer
        res2 = await grounded_answer(s, get_llm(), r, "quantum chromodynamics lattice gauge", RetrievalFilters(collections=["library"]))
        assert res2.insufficient_evidence
