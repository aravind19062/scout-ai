"""Context assembly: turn retrieved chunks into labelled, budgeted, deduplicated prompt passages."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.text import overlap
from app.llm.embeddings import count_tokens
from app.llm.prompts import passage
from app.rag.retriever import RetrievedChunk


@dataclass
class ContextPassage:
    label: str  # e.g. "S1" (source-level label) or "P3" (passage-level label)
    chunk: RetrievedChunk


@dataclass
class AssembledContext:
    passages: list[ContextPassage]
    text: str
    tokens: int

    def by_label(self) -> dict[str, ContextPassage]:
        return {p.label: p for p in self.passages}


def assemble_context(
    chunks: list[RetrievedChunk],
    max_tokens: int = 6000,
    label_prefix: str = "P",
    per_source_labels: bool = False,
    max_per_source: int = 3,
) -> AssembledContext:
    """Greedy packing in relevance order. Drops near-duplicate chunks (e.g. syndicated copies) and caps
    chunks per source so a single long page cannot crowd out others.

    per_source_labels=True gives all chunks from one source the same label (S1, S2...), which is what
    the report/follow-up citations use; otherwise each chunk gets its own label (P1, P2...).
    """
    chosen: list[ContextPassage] = []
    parts: list[str] = []
    used = 0
    per_source: dict = {}
    source_labels: dict = {}
    for ch in chunks:
        if per_source.get(ch.source_id, 0) >= max_per_source:
            continue
        if any(overlap(ch.text, c.chunk.text) > 0.9 for c in chosen):
            continue
        meta = f"{ch.title} | {ch.domain or ch.source_type} | published: {ch.published_at or 'unknown'}"
        if per_source_labels:
            label = source_labels.setdefault(ch.source_id, f"{label_prefix}{len(source_labels) + 1}")
        else:
            label = f"{label_prefix}{len(chosen) + 1}"
        block = passage(label, ch.text, meta)
        n = count_tokens(block)
        if used + n > max_tokens:
            if per_source_labels and ch.source_id in source_labels and per_source.get(ch.source_id, 0) == 0:
                source_labels.pop(ch.source_id)
            continue
        chosen.append(ContextPassage(label, ch))
        parts.append(block)
        used += n
        per_source[ch.source_id] = per_source.get(ch.source_id, 0) + 1
    return AssembledContext(chosen, "\n\n".join(parts), used)
