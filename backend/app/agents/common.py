"""Helpers shared by agents."""

from __future__ import annotations

import math

from app.core.text import normalize, overlap
from app.llm.embeddings import EmbeddingProvider
from app.schemas.llm_outputs import PlannedTask


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def dedupe_tasks(
    candidates: list[PlannedTask],
    existing_objectives: list[str],
    embedder: EmbeddingProvider,
    threshold: float = 0.9,
) -> list[PlannedTask]:
    """Drop tasks whose objective is a near-duplicate of an existing or earlier candidate objective.

    Uses embedding similarity plus a lexical containment check (catches rephrasings either way).
    """
    if not candidates:
        return []
    texts = existing_objectives + [c.objective for c in candidates]
    vecs = await embedder.embed(texts)
    kept_vecs = vecs[: len(existing_objectives)]
    kept_texts = list(existing_objectives)
    out = []
    for cand, v in zip(candidates, vecs[len(existing_objectives) :], strict=True):
        dup = any(cosine(v, kv) >= threshold for kv in kept_vecs) or any(
            normalize(cand.objective) == normalize(t) or (overlap(cand.objective, t) >= 0.9 and overlap(t, cand.objective) >= 0.9)
            for t in kept_texts
        )
        if not dup:
            out.append(cand)
            kept_vecs.append(v)
            kept_texts.append(cand.objective)
    return out


def clean_queries(task: PlannedTask, limit: int) -> list[str]:
    seen, out = set(), []
    for q in task.search_queries + [task.objective]:
        q = " ".join(q.split())[:300]
        if q and normalize(q) not in seen:
            seen.add(normalize(q))
            out.append(q)
    return out[: max(1, limit)]
