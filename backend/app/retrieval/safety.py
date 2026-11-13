"""Heuristic prompt-injection detection for retrieved content.

Detection is defence-in-depth only; the primary protection is architectural (content is always
treated as data, see app/llm/prompts.py). Flagged sources are still usable as evidence, but the
flag is stored, surfaced in the UI, and logged.
"""

from __future__ import annotations

import re

_PATTERNS = {
    "ignore_instructions": r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all|system)\b.{0,20}\b(instructions?|prompts?|rules)\b",
    "role_override": r"\b(you are now|act as|pretend to be|new instructions|system prompt)\b",
    "tool_or_action_request": r"\b(send|email|post|transfer|execute|run)\b.{0,30}\b(this|the following|command|code|money|password|credentials?)\b",
    "exfiltration": r"\b(api[_ ]?key|secret key|access token|password)\b",
    "hidden_markup": r"<\s*(system|assistant|instructions?)\s*>",
}
_COMPILED = {k: re.compile(v, re.I | re.S) for k, v in _PATTERNS.items()}


def detect_injection(text: str) -> list[str]:
    return [name for name, pat in _COMPILED.items() if pat.search(text)]
