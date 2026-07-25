"""Client for the local LLM served by Ollama.

Design rule that the rest of the codebase depends on: **a failed call never
returns content that looks like an answer.** Every method returns an
`LLMResult` carrying an explicit `ok` flag. When `ok` is False the text is
empty and `error` says why.

That rule exists because of a bug this project shipped earlier. The old client
returned a hand-written stub (`{"category": "uncategorized", "confidence": 0.0}`)
whenever the model was unreachable or its output failed to parse. Callers had
no way to tell that stub apart from a real low-confidence answer, so 285
transactions were written to the database tagged `tag_source=llm,
confidence=0.0` when the model had in fact never classified them. The UI then
displayed them as genuine model output. Silent fallbacks that mimic success are
worse than errors.

Two other things this client gets right, both learned by probing the model
directly (see docs/DECISIONS.md §3):

* ``think=False``. The default model is a reasoning model that emits a separate
  `thinking` channel. With thinking left on, JSON generation degenerates into
  repeated-token loops mid-string and long-form prose can take minutes. Turning
  it off makes structured output valid and prose roughly 100x faster.
* JSON **schema**, not ``format="json"``. A bare `format="json"` only asks for
  syntactic JSON, so the model invents its own category names. Passing a real
  JSON Schema with `enum` constraints forces both valid syntax and a value from
  our taxonomy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Retried on: the model is loading, the daemon was just restarted, a transient
# 5xx. Not retried on 4xx — a bad request will fail identically every time.
_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)


class LLMUnavailableError(RuntimeError):
    """Raised by streaming helpers, which have no result object to flag."""


@dataclass(frozen=True)
class LLMResult:
    """Outcome of one model call.

    `ok=True` means the model answered and `text` holds its reply. `ok=False`
    means it did not, `text` is empty, and `error` explains why. There is
    deliberately no third state and no placeholder content.
    """

    text: str
    ok: bool
    error: str | None = None
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok

    def json(self) -> dict[str, Any] | None:
        """Parse `text` as a JSON object, or return None.

        Returns None both when the call failed and when the reply was not a
        JSON object, so callers only need one check. Schema-constrained calls
        make the second case rare but not impossible.
        """
        if not self.ok or not self.text:
            return None
        try:
            parsed = json.loads(self.text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Model returned unparseable JSON: %.200s", self.text)
            return None
        return parsed if isinstance(parsed, dict) else None


class LLMClient:
    """Thin async wrapper over the Ollama chat API."""

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        timeout: float | None = None,
        think: bool | None = None,
    ) -> None:
        s = get_settings()
        self.host = (host or s.llm_host).rstrip("/")
        self.model = model or s.llm_model
        self.vision_model = vision_model or s.llm_vision_model
        self.timeout = timeout or s.llm_timeout_s
        self.think = s.llm_think if think is None else think

    # ---------- health ----------

    async def health(self) -> dict[str, Any]:
        """Report whether the daemon is up and the configured model is pulled.

        Never raises: the health endpoint has to answer even when everything
        downstream is broken.
        """
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                available = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as e:
            return {
                "ok": False,
                "host": self.host,
                "model": self.model,
                "model_pulled": False,
                "error": str(e),
            }
        return {
            "ok": True,
            "host": self.host,
            "model": self.model,
            # Ollama reports names with an explicit tag ("gemma4:26b"), so an
            # untagged config value still matches by prefix.
            "model_pulled": any(n.split(":")[0] == self.model.split(":")[0] for n in available),
            "available_models": available,
        }

    # ---------- completions ----------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.3,
    ) -> LLMResult:
        """Free-text completion. Use for prose, never for data the app parses."""
        return await self._post({
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": {"temperature": temperature},
        })

    async def structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> LLMResult:
        """Schema-constrained completion.

        `schema` is a JSON Schema. Ollama constrains decoding to it, so the
        reply is valid JSON matching the shape — including `enum` values, which
        is how the tagger is held to our category taxonomy. Use `.json()` on
        the result to get the dict.
        """
        return await self._post({
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "format": schema,
            "options": {"temperature": temperature},
        })

    async def vision(
        self,
        prompt: str,
        image_b64: str,
        schema: dict[str, Any] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResult:
        """Multimodal completion over a single base64-encoded image."""
        payload: dict[str, Any] = {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            "think": self.think,
            "options": {"temperature": temperature},
        }
        if schema is not None:
            payload["format"] = schema
        return await self._post(payload)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.4,
    ) -> AsyncIterator[str]:
        """Yield content chunks as they arrive.

        Raises `LLMUnavailableError` instead of yielding apologetic filler, so the
        caller can surface a real error to the client.
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "think": self.think,
            "options": {"temperature": temperature},
        }
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream("POST", f"{self.host}/api/chat", json=payload) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        # Ollama emits one JSON object per line; a partial line
                        # is not fatal, the next one completes the stream.
                        continue
                    if content := chunk.get("message", {}).get("content"):
                        yield content
                    if chunk.get("done"):
                        return
        except Exception as e:
            raise LLMUnavailableError(f"{type(e).__name__}: {e}") from e

    # ---------- transport ----------

    async def _post(self, payload: dict[str, Any], *, max_retries: int = 3) -> LLMResult:
        """POST to /api/chat with exponential backoff on transient failures."""
        t0 = time.perf_counter()
        last_error = "unknown error"

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(f"{self.host}/api/chat", json=payload)
                    r.raise_for_status()
                # `content` excludes the model's `thinking` channel, which is
                # what we want even when thinking is enabled.
                text = r.json().get("message", {}).get("content", "") or ""
                return LLMResult(
                    text=text.strip(),
                    ok=bool(text.strip()),
                    error=None if text.strip() else "model returned empty content",
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            except _RETRYABLE as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < max_retries - 1:
                    backoff = 1.5**attempt
                    logger.info(
                        "LLM transient failure (%d/%d), retrying in %.1fs: %s",
                        attempt + 1, max_retries, backoff, type(e).__name__,
                    )
                    await asyncio.sleep(backoff)
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                is_server_error = 500 <= e.response.status_code < 600
                if is_server_error and attempt < max_retries - 1:
                    await asyncio.sleep(1.5**attempt)
                else:
                    break
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                break

        logger.warning("LLM call failed after %d attempt(s): %s", max_retries, last_error)
        return LLMResult(
            text="",
            ok=False,
            error=last_error,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """Process-wide client. Cheap to construct; shared to keep config in one place."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    """Drop the cached client. Used by tests that patch settings."""
    global _client
    _client = None
