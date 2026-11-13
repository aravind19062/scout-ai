"""Deterministic text utilities shared by verification, chunking and the mock provider."""

from __future__ import annotations

import hashlib
import re
import unicodedata

STOPWORDS = frozenset(
    """a an the and or but if of to in on for with by at from as is are was were be been being this that these
    those it its into than then there their they them we our you your he she his her not no nor so such can
    could would should will shall may might must do does did done have has had having about above after again
    against all also any because before below between both during each few further here how i me more most
    other over own same some through too under until up very what when where which while who whom why
    within without per via across around among including include includes identify analyse analyze analysis
    major key main""".split()
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent)?", re.I)
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    """Lowercase, fold unicode quotes/dashes, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def content_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(normalize(text)) if w not in STOPWORDS and len(w) > 1]


def overlap(a: str, b: str) -> float:
    """Share of `a`'s content words that also appear in `b` (asymmetric containment)."""
    wa = set(content_words(a))
    if not wa:
        return 0.0
    return len(wa & set(content_words(b))) / len(wa)


def extract_numbers(text: str) -> set[str]:
    """Numbers as canonical strings ("1,200" -> "1200", "12.50" -> "12.5"). Years are included."""
    out = set()
    for m in _NUM_RE.finditer(text):
        whole = m.group(1).replace(",", "")
        frac = (m.group(2) or "").rstrip("0")
        out.add(f"{int(whole)}.{frac}" if frac else str(int(whole)))
    return out


def split_sentences(text: str) -> list[str]:
    parts = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if para:
            parts.extend(s.strip() for s in _SENT_RE.split(para) if s.strip())
    return parts


def quote_in_text(quote: str, text: str, min_len: int = 12) -> bool:
    """True if `quote` appears verbatim in `text` (after normalization), allowing elided quotes
    joined by '...' where every fragment must appear in order."""
    q = normalize(quote).strip(" \"'")
    if len(q) < min_len:
        return False
    t = normalize(text)
    fragments = [f.strip(" \"'") for f in re.split(r"\.\.\.|…", q) if f.strip(" \"'")]
    pos = 0
    for frag in fragments:
        idx = t.find(frag, pos)
        if idx < 0:
            return False
        pos = idx + len(frag)
    return True


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def idf_weights(docs: list[str]) -> dict[str, float]:
    """Inverse document frequency of content words across `docs`."""
    import math

    n = len(docs)
    df: dict[str, int] = {}
    for d in docs:
        for w in set(content_words(d)):
            df[w] = df.get(w, 0) + 1
    return {w: math.log(1 + n / c) for w, c in df.items()}


def weighted_overlap(a: str, b: str, weights: dict[str, float], default: float = 1.5) -> float:
    """Like `overlap`, but each word of `a` counts by its IDF weight (rare words matter more)."""
    wa = set(content_words(a))
    if not wa:
        return 0.0
    wb = set(content_words(b))
    total = sum(weights.get(w, default) for w in wa)
    return sum(weights.get(w, default) for w in wa & wb) / total if total else 0.0
