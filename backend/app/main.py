"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import documents, research, system
from app.core.config import get_settings
from app.core.errors import ScoutError
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RateLimitMiddleware
from app.core.tracing import configure_tracing, shutdown_tracing
from app.database.session import dispose_engine, init_engine
from app.orchestration.runner import job_manager

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    problems = s.provider_problems()
    if problems and s.app_env != "test":
        for p in problems:
            log.error("config.invalid", problem=p)
        raise RuntimeError("Invalid provider configuration: " + " | ".join(problems))
    if s.is_mock:
        log.warning("providers.mock_mode", providers=s.mode_summary,
                    note="Mock providers are active (explicitly configured). Results are NOT real research.")
    init_engine()
    n = await job_manager.recover_orphans()
    if n:
        log.warning("jobs.recovered", count=n)
    if s.reranker == "cross_encoder":
        await _warm_reranker()
    log.info("app.started", job_execution=s.job_execution, reranker=s.reranker, demo_mode=s.demo_mode,
             tracing=bool(s.otel_exporter_otlp_endpoint))
    yield
    await job_manager.shutdown()
    await dispose_engine()
    shutdown_tracing()


async def _warm_reranker() -> None:
    """Download/load the cross-encoder at startup instead of on the first query (non-fatal)."""
    import asyncio

    from app.rag.rerank import CrossEncoderReranker

    try:
        await asyncio.to_thread(CrossEncoderReranker(get_settings().reranker_model).warm)
    except Exception as e:
        log.error("reranker.warm_failed", error=str(e))


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="SCOUT AI", version="0.1.0", lifespan=lifespan,
                  description="Autonomous multi-agent research platform: planning, retrieval, verification and cited reports.")
    app.add_middleware(RateLimitMiddleware, per_minute=s.rate_limit_per_minute)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ScoutError)
    async def scout_error(_: Request, exc: ScoutError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errs = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid_request", "message": "Invalid request", "details": errs}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("api.unhandled", error=type(exc).__name__)
        return JSONResponse({"error": {"code": "internal_error", "message": "Internal server error"}}, status_code=500)

    app.include_router(system.router)
    app.include_router(research.router)
    app.include_router(documents.router)
    if configure_tracing():
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # SSE streams are long-lived; don't turn every progress event into a span.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="api/health,api/research/.*/events")
    return app


app = create_app()
