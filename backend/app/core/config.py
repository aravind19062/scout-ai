"""Application configuration, loaded from environment variables (and an optional .env file).

All secrets come from the environment. Nothing in this module has a hardcoded credential.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---------------------------------------------------------------------------------
    app_name: str = "SCOUT AI"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # --- Database ----------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://scout:scout@localhost:5433/scout"
    db_pool_size: int = 10

    # --- Providers ---------------------------------------------------------------------------
    # "mock" providers are deterministic and offline. They are only used when explicitly
    # configured here; the app never silently falls back to them.
    llm_provider: Literal["openai", "mock"] = "openai"
    embedding_provider: Literal["openai", "mock"] = "openai"
    search_provider: Literal["tavily", "mock"] = "tavily"

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_report_model: str | None = None  # defaults to openai_model
    openai_timeout_s: float = 90.0
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536  # must match the migration's vector column size
    embedding_batch_size: int = 64

    tavily_api_key: SecretStr | None = None
    mock_latency_ms: int = 0  # mock LLM only: artificial delay per call, so offline demos stream like real runs

    # Pricing used for cost *estimates*, in USD per 1M tokens. Set these to your actual
    # contract prices; the defaults are placeholders and are labelled as estimates in the UI.
    price_input_per_mtok: float = 0.75
    price_output_per_mtok: float = 4.50
    price_embedding_per_mtok: float = 0.02

    # --- Fetching / security -----------------------------------------------------------------
    fetch_timeout_s: float = 15.0
    fetch_max_bytes: int = 8_000_000
    fetch_max_redirects: int = 5
    fetch_user_agent: str = "ScoutAI-ResearchBot/0.1 (+https://github.com/scout-ai)"
    # Only for local testing against a fixture server. Never enable in production.
    fetch_allow_private_networks: bool = False

    upload_max_mb: int = 20
    rate_limit_per_minute: int = 30  # mutating endpoints, per client IP

    # --- RAG ---------------------------------------------------------------------------------
    chunk_size_tokens: int = 450
    chunk_overlap_tokens: int = 60
    retrieval_candidates: int = 40
    # Default retrieval mode for agents/QA. Choose with `python -m app.evaluation.run retrieval` on real embeddings.
    retrieval_mode: Literal["hybrid", "vector", "keyword"] = "hybrid"  # per retriever (vector / keyword) before fusion

    # --- Orchestration -----------------------------------------------------------------------
    max_concurrent_jobs: int = 3  # per process (API in inline mode, each worker in worker mode)
    llm_max_retries: int = 3
    # "inline": jobs run as asyncio tasks in the API process (single replica, zero extra services).
    # "worker": the API only enqueues; `python -m app.worker` processes claim jobs from Postgres
    # (FOR UPDATE SKIP LOCKED), heartbeat them, and requeue jobs whose worker died.
    job_execution: Literal["inline", "worker"] = "inline"
    worker_poll_s: float = 2.0
    worker_heartbeat_s: float = 10.0
    worker_lease_s: float = 60.0  # a running job without a heartbeat for this long is considered orphaned
    worker_max_attempts: int = 3

    # --- Reranking ---------------------------------------------------------------------------
    # "cross_encoder": local ONNX cross-encoder (fastembed) re-scores the fused candidates.
    # "lexical": dependency-free token-overlap reranker (tests / offline). "none": RRF order only.
    reranker: Literal["none", "cross_encoder", "lexical"] = "none"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 30  # fused candidates passed to the reranker
    # Graceful degradation: if reranking (including waiting for the per-process inference slot) takes
    # longer than this, the first-stage order is used instead. Keeps agents moving on a busy CPU.
    rerank_timeout_s: float = 5.0
    reranker_threads: int | None = None  # ONNX intra-op threads per process (None = onnxruntime default)

    # --- Observability -----------------------------------------------------------------------
    # Traces are exported over OTLP/HTTP when an endpoint is set (e.g. Jaeger at http://jaeger:4318).
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "scout-ai"

    # --- Public demo guardrails --------------------------------------------------------------
    # When enabled: depth is capped, each client gets a daily job quota, and new jobs are refused
    # once today's total estimated spend across all jobs reaches the cap.
    demo_mode: bool = False
    demo_max_depth: Literal["quick", "standard", "deep"] = "quick"
    demo_jobs_per_client_per_day: int = 3
    demo_daily_cost_cap_usd: float = 2.0
    demo_allow_uploads: bool = False

    def provider_problems(self) -> list[str]:
        """Missing-credential problems for the configured providers. Called at app startup
        (not at construction) so tooling such as migrations works without API keys."""
        problems = []
        if (self.llm_provider == "openai" or self.embedding_provider == "openai") and not self.openai_api_key:
            problems.append(
                "OPENAI_API_KEY is required when LLM_PROVIDER/EMBEDDING_PROVIDER=openai. "
                "Set it in .env, or explicitly set the providers to 'mock' for offline mode."
            )
        if self.search_provider == "tavily" and not self.tavily_api_key:
            problems.append(
                "TAVILY_API_KEY is required when SEARCH_PROVIDER=tavily. "
                "Set it in .env, or explicitly set SEARCH_PROVIDER=mock for offline mode."
            )
        return problems

    @property
    def report_model(self) -> str:
        return self.openai_report_model or self.openai_model

    @property
    def is_mock(self) -> bool:
        return "mock" in (self.llm_provider, self.embedding_provider, self.search_provider)

    @property
    def mode_summary(self) -> dict[str, str]:
        return {
            "llm": self.llm_provider,
            "embeddings": self.embedding_provider,
            "search": self.search_provider,
            "model": self.openai_model if self.llm_provider == "openai" else "mock",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
