"""Second-stage reranking of retrieved chunks.

First-stage retrieval (vector + keyword, fused with RRF) is optimised for recall: cheap, but it scores
query and passage independently. A cross-encoder reads the query and each candidate passage *together*
and is far more precise, but too slow to run over the whole index, so it re-scores only the top
`RERANK_CANDIDATES` fused hits.

  * cross_encoder: local ONNX model via fastembed (default ms-marco-MiniLM-L-6-v2, ~80 MB, CPU-only,
    no PyTorch, no API cost). Install with `uv sync --extra rerank`.
  * lexical: dependency-free query-term coverage scorer; deterministic, used in tests and as a baseline.
  * none: keep the RRF order.

Which one helps is an empirical question: `python -m app.evaluation.run retrieval` reports every
retrieval mode with and without reranking on the labelled set.
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import get_settings
from app.core.errors import ScoutError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.rag.retriever import RetrievedChunk

log = get_logger(__name__)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have how in is it its of on or that the this to was were what when where which who why will with".split())


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, hits: list[RetrievedChunk], k: int) -> list[RetrievedChunk]: ...


def _ordered(hits: list[RetrievedChunk], scores: list[float], k: int) -> list[RetrievedChunk]:
    # Stable on ties: equal scores keep their first-stage (RRF) order.
    order = sorted(range(len(hits)), key=lambda i: (-scores[i], i))[:k]
    return [replace(hits[i], rerank_score=float(scores[i])) for i in order]


class NoReranker:
    name = "none"

    async def rerank(self, query: str, hits: list[RetrievedChunk], k: int) -> list[RetrievedChunk]:
        return hits[:k]


class LexicalReranker:
    """Fraction of (IDF-weighted, within the candidate set) query terms a passage covers, with a small
    bonus for terms appearing in the title. Cheap and deterministic; not a substitute for a model."""

    name = "lexical"

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]

    async def rerank(self, query: str, hits: list[RetrievedChunk], k: int) -> list[RetrievedChunk]:
        q = set(self._terms(query))
        if not q or not hits:
            return hits[:k]
        docs = [set(self._terms(h.text)) for h in hits]
        titles = [set(self._terms(h.title or "")) for h in hits]
        n = len(hits)
        idf = {t: math.log(1 + n / (1 + sum(t in d for d in docs))) for t in q}
        total = sum(idf.values()) or 1.0
        scores = [(sum(idf[t] for t in q if t in d) + 0.25 * sum(idf[t] for t in q if t in ti)) / total
                  for d, ti in zip(docs, titles, strict=True)]
        return _ordered(hits, scores, k)


# One model per process, loaded once even when several jobs ask for it at the same moment.
_models: dict[str, Any] = {}
_load_lock = threading.Lock()
# One inference at a time per process: ONNX already parallelises a batch across cores, so running
# several concurrently (one per research job) only makes them all slower. A thread-level lock (not an
# asyncio one) because inference keeps running in its thread even after the awaiting caller times out.
_infer_lock = threading.Lock()


class CrossEncoderReranker:
    name = "cross_encoder"

    def __init__(self, model_name: str):
        self.model_name = model_name

    def _model(self):
        if (m := _models.get(self.model_name)) is not None:
            return m
        with _load_lock:
            if self.model_name not in _models:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder
                except ImportError as e:  # pragma: no cover - depends on the optional extra
                    raise ScoutError("RERANKER=cross_encoder needs the 'rerank' extra: uv sync --extra rerank") from e
                _models[self.model_name] = TextCrossEncoder(self.model_name, threads=get_settings().reranker_threads)
            return _models[self.model_name]

    def _score(self, query: str, texts: list[str], deadline: float) -> list[float] | None:
        with _infer_lock:
            if time.monotonic() > deadline:
                return None  # the caller already fell back; don't burn CPU on an abandoned request
            return [float(x) for x in self._model().rerank(query, texts, batch_size=32)]

    async def rerank(self, query: str, hits: list[RetrievedChunk], k: int) -> list[RetrievedChunk]:
        if not hits:
            return []
        timeout = get_settings().rerank_timeout_s
        try:
            # CPU-bound ONNX inference: keep it off the event loop.
            scores = await asyncio.wait_for(
                asyncio.to_thread(self._score, query, [h.text for h in hits], time.monotonic() + timeout), timeout)
        except TimeoutError:
            scores = None
        if scores is None:
            # Degrade, don't fail: the first-stage (RRF) order is still a good ranking.
            log.warning("rerank.timeout_fallback", candidates=len(hits), timeout_s=timeout)
            return hits[:k]
        return _ordered(hits, scores, k)

    def warm(self) -> None:
        self._model()


def get_reranker(name: str | None = None) -> Reranker:
    s = get_settings()
    name = name or s.reranker  # explicit "none" disables a configured reranker
    if name == "cross_encoder":
        return CrossEncoderReranker(s.reranker_model)
    if name == "lexical":
        return LexicalReranker()
    return NoReranker()
