"""Token-aware, paragraph-respecting chunking with configurable overlap."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import split_sentences
from app.llm.embeddings import _encoding


@dataclass
class TextChunk:
    ordinal: int
    text: str
    token_count: int


def _units(text: str, max_tokens: int) -> list[tuple[str, int]]:
    """Split into paragraphs; split oversized paragraphs into sentences, then hard-split by tokens."""
    enc = _encoding()
    units: list[tuple[str, int]] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        n = len(enc.encode(para, disallowed_special=()))
        if n <= max_tokens:
            units.append((para, n))
            continue
        for sent in split_sentences(para):
            toks = enc.encode(sent, disallowed_special=())
            if len(toks) <= max_tokens:
                units.append((sent, len(toks)))
            else:
                for i in range(0, len(toks), max_tokens):
                    piece = toks[i : i + max_tokens]
                    units.append((enc.decode(piece), len(piece)))
    return units


def chunk_text(text: str, chunk_size: int = 450, overlap: int = 60) -> list[TextChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    units = _units(text, chunk_size)
    chunks: list[TextChunk] = []
    current: list[tuple[str, int]] = []
    cur_tokens = 0

    def flush():
        if current:
            chunks.append(TextChunk(len(chunks), "\n\n".join(u for u, _ in current), sum(n for _, n in current)))

    for unit, n in units:
        if current and cur_tokens + n > chunk_size:
            flush()
            # Carry trailing units forward as overlap (whole units, so chunks start cleanly).
            carried, carried_tokens = [], 0
            for u, un in reversed(current):
                if carried_tokens + un > overlap:
                    break
                carried.insert(0, (u, un))
                carried_tokens += un
            current, cur_tokens = carried, carried_tokens
        current.append((unit, n))
        cur_tokens += n
    flush()
    return chunks
