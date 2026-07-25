"""The LLM client's core contract: failure never looks like an answer.

These are regression tests for a real bug. The previous client returned a
hand-written stub — `{"category": "uncategorized", "confidence": 0.0}` — whenever
the model was unreachable or its reply failed to parse. Callers could not tell
that apart from a real low-confidence answer, so 285 unclassified transactions
were stored as model output and shown to the user as such.
"""
from __future__ import annotations

import httpx
import pytest

from app.llm.client import LLMClient, LLMResult, LLMUnavailableError


def test_failed_result_carries_no_content():
    result = LLMResult(text="", ok=False, error="connection refused")
    assert result.failed
    assert result.text == ""
    assert result.json() is None


def test_unparseable_reply_returns_none_not_a_default():
    """A reply that is not JSON yields None, never a plausible-looking dict."""
    result = LLMResult(text="{'category': broken", ok=True)
    assert result.json() is None


def test_json_array_reply_is_rejected():
    """The schema always describes an object; a bare array is not usable."""
    assert LLMResult(text="[1, 2, 3]", ok=True).json() is None


def test_successful_json_parses():
    result = LLMResult(text='{"category": "food", "confidence": 0.9}', ok=True)
    assert result.json() == {"category": "food", "confidence": 0.9}


@pytest.mark.asyncio
async def test_unreachable_host_returns_failure(monkeypatch):
    """A connection error becomes ok=False, not an exception and not filler."""
    client = LLMClient(host="http://127.0.0.1:1", timeout=0.05)

    async def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", refuse)
    # No sleeping through the retry backoff.
    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    result = await client.complete([{"role": "user", "content": "hello"}])
    assert result.failed
    assert result.text == ""
    assert "ConnectError" in (result.error or "")


@pytest.mark.asyncio
async def test_empty_content_counts_as_failure(monkeypatch):
    """A 200 response with no content is a failure, not an empty answer."""
    client = LLMClient(host="http://fake", timeout=1)

    async def respond(*args, **kwargs):
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": ""}},
            request=httpx.Request("POST", "http://fake/api/chat"),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    result = await client.complete([{"role": "user", "content": "hello"}])
    assert result.failed
    assert result.error == "model returned empty content"


@pytest.mark.asyncio
async def test_thinking_channel_is_not_treated_as_the_answer(monkeypatch):
    """`thinking` is discarded; only `content` is the reply.

    The default model is a reasoning model. Its thinking trace is not an answer
    and must never reach a parser.
    """
    client = LLMClient(host="http://fake", timeout=1)

    async def respond(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", "http://fake/api/chat"), json={"message": {
            "role": "assistant",
            "content": '{"category": "food", "confidence": 0.9, "reason": "ok"}',
            "thinking": "Let me think about whether this is food or transport...",
        }})

    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    result = await client.structured([{"role": "user", "content": "x"}], schema={})
    assert result.json() == {"category": "food", "confidence": 0.9, "reason": "ok"}
    assert "Let me think" not in result.text


@pytest.mark.asyncio
async def test_client_disables_thinking_by_default(monkeypatch):
    """think=False must be sent: with thinking on, structured output degenerates."""
    client = LLMClient(host="http://fake", timeout=1)
    captured: dict = {}

    async def respond(self, url, json=None, **kwargs):
        captured.update(json or {})
        return httpx.Response(200, json={"message": {"content": "ok"}}, request=httpx.Request("POST", "http://fake/api/chat"))

    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    await client.complete([{"role": "user", "content": "x"}])
    assert captured["think"] is False


@pytest.mark.asyncio
async def test_schema_is_sent_as_format(monkeypatch):
    """Structured calls must pass the schema, not the string "json".

    `format="json"` only asks for syntactic JSON, and the model then invents its
    own category names. The enum in the schema is what holds it to the taxonomy.
    """
    client = LLMClient(host="http://fake", timeout=1)
    schema = {"type": "object", "properties": {"category": {"enum": ["food"]}}}
    captured: dict = {}

    async def respond(self, url, json=None, **kwargs):
        captured.update(json or {})
        return httpx.Response(200, json={"message": {"content": '{"category": "food"}'}}, request=httpx.Request("POST", "http://fake/api/chat"))

    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    await client.structured([{"role": "user", "content": "x"}], schema=schema)
    assert captured["format"] == schema


@pytest.mark.asyncio
async def test_retries_then_gives_up(monkeypatch):
    client = LLMClient(host="http://fake", timeout=1)
    attempts = 0

    async def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.TimeoutException("too slow")

    monkeypatch.setattr(httpx.AsyncClient, "post", flaky)
    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    result = await client.complete([{"role": "user", "content": "x"}])
    assert attempts == 3
    assert result.failed


@pytest.mark.asyncio
async def test_client_errors_are_not_retried(monkeypatch):
    """A 400 will fail identically every time; retrying it wastes the user's time."""
    client = LLMClient(host="http://fake", timeout=1)
    attempts = 0

    async def bad_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, text="bad schema", request=httpx.Request("POST", "http://fake"))

    monkeypatch.setattr(httpx.AsyncClient, "post", bad_request)
    result = await client.complete([{"role": "user", "content": "x"}])
    assert attempts == 1
    assert result.failed


@pytest.mark.asyncio
async def test_stream_raises_instead_of_yielding_apology(monkeypatch):
    """Streaming has no result object, so it must raise rather than fake prose."""
    client = LLMClient(host="http://127.0.0.1:1", timeout=0.05)

    with pytest.raises(LLMUnavailableError):
        async for _ in client.stream([{"role": "user", "content": "hi"}]):
            pass


@pytest.mark.asyncio
async def test_health_never_raises():
    client = LLMClient(host="http://127.0.0.1:1", timeout=0.05)
    health = await client.health()
    assert health["ok"] is False
    assert "error" in health


async def _no_sleep(_seconds):
    return None
