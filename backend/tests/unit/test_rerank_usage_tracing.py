from dataclasses import dataclass

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from app.llm.base import LLMResponse
from app.llm.gateway import LLMGateway
from app.llm.usage import Limits, UsageTracker, bind_usage, unbind_usage
from app.rag.rerank import LexicalReranker, get_reranker
from app.rag.retriever import RetrievedChunk

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _tracer_provider():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)  # the app's ProxyTracer picks this up
    yield


def _hit(i, text, title=""):
    import uuid

    return RetrievedChunk(chunk_id=uuid.uuid4(), source_id=uuid.uuid4(), text=text, score=1 / (i + 1), vector_similarity=None,
                          keyword_rank=None, vector_rank=i, title=title, url=None, domain=None, source_type="web",
                          published_at=None, retrieved_at="2026-01-01T00:00:00", security_flags=[])


async def test_lexical_reranker_promotes_passages_covering_the_query():
    hits = [_hit(0, "Unrelated text about weather."), _hit(1, "Battery plants drew record investment in 2025."),
            _hit(2, "Battery chemistry overview."), _hit(3, "More unrelated text.")]
    out = await LexicalReranker().rerank("battery investment", hits, k=3)
    assert out[0].text.startswith("Battery plants") and out[1].text.startswith("Battery chemistry")
    assert out[0].rerank_score > out[1].rerank_score > out[2].rerank_score
    # ties keep first-stage order
    same = await LexicalReranker().rerank("zzz", hits, k=4)
    assert [h.text for h in same] == [h.text for h in hits]


def test_get_reranker_none_by_default():
    assert get_reranker().name == "none" and get_reranker("lexical").name == "lexical"


def test_usage_by_stage_roundtrip():
    t = UsageTracker(limits=Limits())
    t.stage = "plan"
    t.record_llm(1000, 200)
    t.stage = "research"
    t.record_tool("search", True)
    t.record_embedding(500)
    t.record_llm(10, 10, cached=True)
    t.add_stage_time("research", 2.5)
    snap = t.snapshot()
    assert "stage" not in snap
    assert snap["by_stage"]["plan"]["llm_calls"] == 1 and snap["by_stage"]["research"]["tool_calls"] == 1
    assert snap["by_stage"]["research"]["llm_cache_hits"] == 1 and snap["by_stage"]["research"]["elapsed_s"] == 2.5
    assert abs(sum(b["cost_usd"] for b in snap["by_stage"].values()) - snap["cost_usd"]) < 1e-6
    r = UsageTracker.restore(snap, Limits())
    assert r.by_stage["plan"]["prompt_tokens"] == 1000


class Out(BaseModel):
    answer: str


@dataclass
class FakeProvider:
    name: str = "fake"

    async def structured(self, req, schema):
        return LLMResponse(parsed=schema(answer="ok"), prompt_tokens=120, completion_tokens=30, model="fake-1")


async def test_llm_calls_emit_genai_spans_without_prompt_content():
    _exporter.clear()
    usage = UsageTracker()
    tok = bind_usage(usage)
    try:
        await LLMGateway(FakeProvider(), use_cache=False).structured("plan", "SECRET SYSTEM", "SECRET USER", Out, model="fake-1")
    finally:
        unbind_usage(tok)
    (sp,) = [s for s in _exporter.get_finished_spans() if s.name == "llm.plan"]
    a = dict(sp.attributes)
    assert a["gen_ai.request.model"] == "fake-1" and a["gen_ai.usage.input_tokens"] == 120
    assert a["gen_ai.usage.output_tokens"] == 30 and a["scout.cache_hit"] is False and a["scout.cost_usd"] > 0
    assert not any("SECRET" in str(v) for v in a.values())


async def test_cross_encoder_falls_back_to_first_stage_order_on_timeout(monkeypatch):
    import time as _time

    from app.core.config import get_settings
    from app.rag import rerank as rr

    monkeypatch.setattr(get_settings(), "rerank_timeout_s", 0.2)

    class SlowModel:
        def rerank(self, query, texts, batch_size=32):
            _time.sleep(0.6)
            return [float(i) for i in range(len(texts))]  # would reverse the order

    monkeypatch.setitem(rr._models, "slow-model", SlowModel())
    hits = [_hit(i, f"passage {i}") for i in range(4)]
    out = await rr.CrossEncoderReranker("slow-model").rerank("q", hits, k=3)
    assert [h.text for h in out] == ["passage 0", "passage 1", "passage 2"] and all(h.rerank_score is None for h in out)

    # A request queued behind the slow one whose deadline passed is skipped, not computed.
    assert rr.CrossEncoderReranker("slow-model")._score("q", ["x"], deadline=_time.monotonic() - 1) is None
