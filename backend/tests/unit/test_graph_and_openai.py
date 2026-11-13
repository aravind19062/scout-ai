import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.llm.base import LLMRequest
from app.llm.openai_provider import OpenAIProvider
from app.orchestration.graph import STAGES, entry_for
from app.schemas.llm_outputs import SectionDraft


def test_entry_for_resume():
    for st in STAGES:
        assert entry_for(st) == st
    assert entry_for("done") == "plan" and entry_for("bogus") == "plan"


def _provider(handler) -> OpenAIProvider:
    s = get_settings().model_copy(update={"openai_api_key": SecretStr("sk-test-0000000000000000"), "llm_max_retries": 2})
    p = OpenAIProvider(s)
    from openai import AsyncOpenAI

    p._client = AsyncOpenAI(api_key="sk-test-0000000000000000", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=0)
    return p


def _completion(content: str | None, refusal: str | None = None):
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content, "refusal": refusal}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


REQ = LLMRequest(task="report_section", system="s", user="u", model="gpt-test", inputs={})


async def test_openai_structured_output_and_usage():
    seen = {}

    def handler(req: httpx.Request):
        import json

        body = json.loads(req.content)
        seen["format"] = body["response_format"]["type"]
        seen["strict"] = body["response_format"]["json_schema"]["strict"]
        return httpx.Response(200, json=_completion('{"markdown": "Hello [S1]"}'))

    r = await _provider(handler).structured(REQ, SectionDraft)
    assert isinstance(r.parsed, SectionDraft) and r.parsed.markdown == "Hello [S1]"
    assert (r.prompt_tokens, r.completion_tokens) == (11, 7)
    assert seen == {"format": "json_schema", "strict": True}


async def test_openai_retries_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"message": "boom"}}) if calls["n"] == 1 else httpx.Response(200, json=_completion('{"markdown": "ok"}'))

    r = await _provider(handler).structured(REQ, SectionDraft)
    assert r.parsed.markdown == "ok" and calls["n"] == 2


async def test_openai_refusal_and_auth_errors():
    with pytest.raises(ProviderError, match="refused"):
        await _provider(lambda r: httpx.Response(200, json=_completion(None, "I can't help"))).structured(REQ, SectionDraft)
    with pytest.raises(ProviderError):
        await _provider(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})).structured(REQ, SectionDraft)
