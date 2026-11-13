"""Structured logging via structlog, with secret redaction."""

from __future__ import annotations

import logging
import re
import sys

import structlog

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"),
]
_SECRET_KEYS = {"api_key", "openai_api_key", "tavily_api_key", "authorization", "password", "token"}


def redact(value: str) -> str:
    for pat in _SECRET_PATTERNS:
        value = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", value)
    return value


def _redact_processor(_logger, _method, event_dict):
    for key, val in list(event_dict.items()):
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = "[REDACTED]"
        elif isinstance(val, str):
            event_dict[key] = redact(val)
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    for noisy in ("httpx", "httpcore", "openai", "trafilatura", "htmldate"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
