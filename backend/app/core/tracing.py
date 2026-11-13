"""OpenTelemetry tracing.

One trace per research job, with child spans per agent stage, LLM call, tool call and retrieval:

    research.job ─┬─ agent.plan ── llm.plan
                  ├─ agent.research ─┬─ tool.search ─ tool.fetch ─ rag.retrieve ─ llm.extract_findings …
                  ├─ agent.verify ── rag.retrieve ─ llm.judge_evidence …
                  └─ agent.report ── llm.report_section …

LLM spans carry OpenTelemetry GenAI semantic-convention attributes (model, input/output tokens) plus
`scout.cost_usd` and `scout.cache_hit`. Prompts and responses are never recorded.

Export is opt-in: set OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://jaeger:4318). Without it, the API-only
tracer is a no-op, so instrumentation costs nothing in tests and local runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
tracer = trace.get_tracer("scout_ai")
_configured = False


def configure_tracing(service_name: str | None = None) -> bool:
    """Install an OTLP exporter if an endpoint is configured. Returns True when export is active."""
    global _configured
    s = get_settings()
    if _configured or not s.otel_exporter_otlp_endpoint:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name or s.otel_service_name,
                                                        "deployment.environment": s.app_env}))
    endpoint = s.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    log.info("tracing.enabled", endpoint=endpoint)
    return True


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a child span; exceptions are recorded and re-raised. None-valued attributes are skipped."""
    with tracer.start_as_current_span(name, record_exception=True, set_status_on_exception=False) as sp:
        set_attrs(sp, **attributes)
        try:
            yield sp
        except BaseException as e:
            sp.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


def set_attrs(sp: Span, **attributes: Any) -> None:
    for k, v in attributes.items():
        if v is not None:
            sp.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
