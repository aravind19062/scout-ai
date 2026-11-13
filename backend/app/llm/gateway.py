"""LLM gateway: the single entry point agents use for model calls.

Adds, around any provider:
  * budget enforcement (checked before each call),
  * usage/cost accounting into the active job's UsageTracker,
  * a content-addressed response cache in Postgres (re-running or resuming a stage does not pay twice),
  * structured logging without prompt contents or secrets.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings, get_settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.core.text import sha256
from app.core.tracing import set_attrs, span
from app.database.session import session_scope
from app.llm.base import LLMProvider, LLMRequest, T
from app.llm.usage import current_usage
from app.models import LLMCacheEntry

log = get_logger(__name__)


class LLMGateway:
    def __init__(self, provider: LLMProvider, settings: Settings | None = None, use_cache: bool = True):
        self.provider = provider
        self.settings = settings or get_settings()
        self.use_cache = use_cache

    def _key(self, req: LLMRequest, schema: type) -> str:
        payload = {
            "provider": self.provider.name,
            "model": req.model,
            "task": req.task,
            "system": req.system,
            "user": req.user,
            "schema": schema.model_json_schema(),
        }
        if self.provider.name == "mock":  # mock output depends on inputs, not the rendered prompt
            payload["inputs"] = req.inputs
        return sha256(json.dumps(payload, sort_keys=True, default=str))

    async def structured(
        self,
        task: str,
        system: str,
        user: str,
        schema: type[T],
        inputs: dict[str, Any] | None = None,
        model: str | None = None,
        max_output_tokens: int = 4000,
    ) -> T:
        usage = current_usage()
        usage.check()
        req = LLMRequest(
            task=task,
            system=system,
            user=user,
            model=model or self.settings.openai_model,
            inputs=inputs or {},
            max_output_tokens=max_output_tokens,
        )
        key = self._key(req, schema)
        with span(f"llm.{task}", **{"gen_ai.operation.name": "chat", "gen_ai.system": self.provider.name,
                                    "gen_ai.request.model": req.model, "scout.task": task,
                                    "gen_ai.request.max_tokens": max_output_tokens}) as sp:
            return await self._structured(req, schema, key, usage, sp)

    async def _structured(self, req: LLMRequest, schema: type[T], key: str, usage, sp) -> T:
        task = req.task
        if self.use_cache:
            async with session_scope() as s:
                hit = (await s.execute(select(LLMCacheEntry).where(LLMCacheEntry.key == key))).scalar_one_or_none()
            if hit is not None:
                usage.record_llm(hit.prompt_tokens, hit.completion_tokens, cached=True)
                set_attrs(sp, **{"scout.cache_hit": True, "gen_ai.usage.input_tokens": hit.prompt_tokens,
                                 "gen_ai.usage.output_tokens": hit.completion_tokens, "scout.cost_usd": 0.0})
                log.debug("llm.cache_hit", task=task)
                return schema.model_validate(hit.response)

        t0 = time.monotonic()
        try:
            resp = await self.provider.structured(req, schema)
        except ProviderError:
            usage.llm_failures += 1
            raise
        usage.record_llm(resp.prompt_tokens, resp.completion_tokens)
        set_attrs(sp, **{"scout.cache_hit": False, "gen_ai.response.model": resp.model,
                         "gen_ai.usage.input_tokens": resp.prompt_tokens, "gen_ai.usage.output_tokens": resp.completion_tokens,
                         "scout.cost_usd": round(usage.llm_cost(resp.prompt_tokens, resp.completion_tokens), 6)})
        log.info(
            "llm.call",
            task=task,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            ms=int((time.monotonic() - t0) * 1000),
        )

        if self.use_cache:
            async with session_scope() as s:
                await s.execute(
                    insert(LLMCacheEntry)
                    .values(
                        key=key,
                        task=task,
                        model=resp.model,
                        response=resp.parsed.model_dump(mode="json"),
                        prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens,
                    )
                    .on_conflict_do_nothing()
                )
        return resp.parsed
