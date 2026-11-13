"""Evaluation metrics. Each function documents exactly what it measures.

Deterministic metrics: precision/recall@k, MRR, fact recall (keyword match), citation URL validity.
Model-assisted metrics (faithfulness, citation support) reuse the verifier's judge prompt plus the
programmatic quote check, so a "support" verdict always has a verbatim quote behind it. They are
still judged by an LLM: in live mode they inherit judge error, and judging with the same model
family as the generator can bias scores upward (documented in docs/EVALUATION.md).
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import cosine
from app.agents.verifier import check_judgement
from app.core.text import content_words, normalize, split_sentences
from app.llm.embeddings import EmbeddingProvider
from app.llm.gateway import LLMGateway
from app.llm.prompts import VERIFIER_SYSTEM, passage
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.schemas.llm_outputs import EvidenceJudgements

# ---------------------------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------------------------


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Share of the top-k retrieved documents that are relevant."""
    top = ranked[:k]
    return sum(1 for d in top if d in relevant) / k if k else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Share of all relevant documents that appear in the top-k."""
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def mrr(ranked: list[str], relevant: set[str]) -> float:
    """Reciprocal rank of the first relevant document (0 if none retrieved)."""
    for i, d in enumerate(ranked, 1):
        if d in relevant:
            return 1 / i
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Binary-relevance nDCG@k: rewards relevant documents ranked higher."""
    dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(ranked[:k], 1) if d in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal if ideal else 0.0


# ---------------------------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------------------------

_LINK = re.compile(r"\[(S\d+)\]\([^)]*\)")
_CITE = re.compile(r"\[(S\d+)\]")


def clean_answer(md: str) -> str:
    """Strip Markdown link targets so '[S1](url)' becomes '[S1]'."""
    return _LINK.sub(r"[\1]", md)


def fact_recall(answer: str, facts: list[dict]) -> tuple[float, list[str]]:
    """Share of reference facts whose keywords ALL appear in the answer (case/format-insensitive).
    A lexical proxy for completeness/correctness: misses paraphrases, never rewards absent facts."""
    a = normalize(answer).replace(",", "")
    hit = [f["fact"] for f in facts if all(normalize(k).replace(",", "") in a for k in f["keywords"])]
    return (len(hit) / len(facts) if facts else 0.0), hit


def answer_sentences(answer: str, min_words: int = 5) -> list[str]:
    text = re.sub(r"^#+\s.*$", "", clean_answer(answer), flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)
    return [s for s in split_sentences(text) if len(content_words(_CITE.sub("", s))) >= min_words]


async def answer_relevance(embedder: EmbeddingProvider, question: str, answer: str) -> float:
    """Cosine similarity between question and answer embeddings. A coarse topicality proxy only:
    it does not measure correctness."""
    q, a = await embedder.embed([question, clean_answer(answer)[:6000]])
    return cosine(q, a)


@dataclass
class Judgement:
    supported: bool
    stance: str


async def judge_against(
    session: AsyncSession, llm: LLMGateway, retriever: HybridRetriever, statement: str, filters: RetrievalFilters, k: int = 4
) -> Judgement:
    """Is `statement` supported by passages from the given scope? Uses the verifier judge and
    requires a verbatim quote (checked in code) for a positive verdict."""
    chunks = await retriever.search(session, _CITE.sub("", statement), filters, k=k)
    if not chunks:
        return Judgement(False, "no_context")
    labels = {f"P{i}": c for i, c in enumerate(chunks, 1)}
    out = await llm.structured(
        task="judge_evidence", system=VERIFIER_SYSTEM,
        user=f"Claim: {statement}\n\nPassages:\n" + "\n\n".join(passage(lbl, c.text) for lbl, c in labels.items()),
        schema=EvidenceJudgements, inputs={"claim": statement, "passages": [{"id": lbl, "text": c.text} for lbl, c in labels.items()]},
    )
    best = "irrelevant"
    for j in out.judgements:
        c = labels.get(j.passage_id.strip())
        if c is None:
            continue
        stance, _, _ = check_judgement(statement, j.stance, j.quote, c.text)
        if stance == "supports":
            return Judgement(True, "supports")
        if stance == "partial":
            best = "partial"
        elif stance == "contradicts" and best == "irrelevant":
            best = "contradicts"
    return Judgement(best == "partial", best)


async def faithfulness(session, llm, retriever, answer: str, filters: RetrievalFilters, max_sentences: int = 12) -> tuple[float | None, int]:
    """Share of substantive answer sentences supported (fully or partially) by passages from the
    system's own retrieved corpus. None when there is no corpus (direct LLM baseline)."""
    sents = answer_sentences(answer)[:max_sentences]
    if not sents:
        return None, 0
    ok = [((await judge_against(session, llm, retriever, s, filters)).supported) for s in sents]
    return sum(ok) / len(ok), len(ok)


async def citation_support(
    session, llm, retriever, answer: str, label_to_source: dict[str, uuid.UUID], collection: str, max_pairs: int = 15
) -> tuple[float | None, int]:
    """Share of (sentence, cited source) pairs where the CITED source itself supports the sentence."""
    pairs = []
    for s in answer_sentences(answer, min_words=3):
        for lbl in dict.fromkeys(_CITE.findall(s)):
            if lbl in label_to_source:
                pairs.append((s, label_to_source[lbl]))
    pairs = pairs[:max_pairs]
    if not pairs:
        return None, 0
    ok = [
        (await judge_against(session, llm, retriever, s, RetrievalFilters(collections=[collection], source_ids=[sid]), k=3)).supported
        for s, sid in pairs
    ]
    return sum(ok) / len(ok), len(ok)


async def url_valid(url: str) -> bool:
    """Cited URL resolves (HTTP < 400 via the SSRF-safe fetcher). Fixture URLs are checked against
    the fixture corpus instead of the network."""
    from app.core.config import get_settings
    from app.core.errors import ScoutError
    from app.retrieval.fetcher import FixtureFetcher, HttpFetcher

    fetcher = FixtureFetcher() if get_settings().search_provider == "mock" else HttpFetcher()
    try:
        await fetcher.fetch(url)
        return True
    except ScoutError:
        return False
