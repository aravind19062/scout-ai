"""Citation validation and rendering.

The model cites with labels like [S3]. We (1) strip labels that don't correspond to a provided
source, so the model cannot invent references, and (2) render valid labels as Markdown links to
the real URL we retrieved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CITE_RE = re.compile(r"\[(S\d+)\](?!\()")


@dataclass
class CitationCheck:
    text: str
    used: list[str]
    removed: list[str]


def validate_citations(text: str, valid_labels: set[str]) -> CitationCheck:
    used: list[str] = []
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        label = m.group(1)
        if label in valid_labels:
            if label not in used:
                used.append(label)
            return m.group(0)
        removed.append(label)
        return ""

    cleaned = CITE_RE.sub(repl, text)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)
    return CitationCheck(cleaned, used, removed)


def linkify(text: str, urls: dict[str, str | None]) -> str:
    def repl(m: re.Match) -> str:
        label = m.group(1)
        url = urls.get(label)
        return f"[{label}]({url})" if url else f"[{label}]"

    return CITE_RE.sub(repl, text)


def relabel(text: str, mapping: dict[str, str]) -> str:
    """Map local labels (e.g. per-prompt S1..Sn) to global report labels."""
    return CITE_RE.sub(lambda m: f"[{mapping.get(m.group(1), m.group(1))}]", text)
