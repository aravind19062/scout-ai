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
    retrieval_candidates: int = 40  # per retriever (vector / keyword) before fusion

    # --- Orchestration -----------------------------------------------------------------------
    max_concurrent_jobs: int = 3
    llm_max_retries: int = 3

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
