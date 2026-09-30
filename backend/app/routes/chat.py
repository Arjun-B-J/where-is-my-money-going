"""Chat over your own spending.

The work happens in `app.agent`: the model chooses queries, the tools compute
every figure, and each answer is checked for figures that nothing computed
before it is sent (DECISIONS.md §15). This module turns that into two endpoints
and keeps one rule of its own: a failure is sent as an error, never as prose
that could be mistaken for an answer.

The answer arrives as one chunk rather than token by token. It cannot be shown
until its figures have been checked, and a figure already on screen cannot be
taken back. The tool events stream as the queries run, so the wait is visible.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.loop import ToolStep, answer, stream_answer
from app.db import get_db
from app.llm.client import get_llm
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

MODEL_UNREACHABLE = "The local model is not reachable."
UNEXPECTED_FAILURE = "Something went wrong while answering. The backend log has the details."


def _history(request: ChatRequest) -> list[dict[str, str]]:
    """The conversation, without any system message the client sent.

    The server's system prompt carries the rules that keep figures honest, and
    a client-supplied one could countermand them.
    """
    return [
        {"role": message.role, "content": message.content}
        for message in request.messages if message.role != "system"
    ]


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/stream")
async def chat_stream(
    request: ChatRequest, db: Session = Depends(get_db)
) -> StreamingResponse:
    """Server-sent events, in this order:

    * `{"tool": {"name", "args", "ok"}}` as each query runs
    * `{"delta": "..."}` with the checked answer
    * `{"done": true, "grounded": bool, "ungrounded": [...], "mode": "agent" | "summary"}`

    A failure sends one `{"error": ...}` in place of the last two.
    """
    llm = get_llm()
    history = _history(request)

    async def events() -> AsyncIterator[str]:
        try:
            async for item in stream_answer(llm, db, history):
                if isinstance(item, ToolStep):
                    yield _event({"tool": {"name": item.tool, "args": item.args, "ok": item.ok}})
                elif item.ok:
                    yield _event({"delta": item.text})
                    yield _event({
                        "done": True, "grounded": item.grounded,
                        "ungrounded": item.ungrounded, "mode": item.mode,
                    })
                else:
                    logger.warning("Chat failed: %s", item.error)
                    yield _event({"error": MODEL_UNREACHABLE})
        except Exception:
            # Headers have gone out by now, so a 500 is no longer possible. An
            # error event is the only way left to say that this is not an answer.
            logger.exception("Chat stream failed")
            yield _event({"error": UNEXPECTED_FAILURE})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("")
async def chat(request: ChatRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Non-streaming variant, with the same checks and the full trace."""
    result = await answer(get_llm(), db, _history(request))
    if not result.ok:
        logger.warning("Chat failed: %s", result.error)
    return {
        "ok": result.ok,
        "reply": result.text if result.ok else None,
        "error": None if result.ok else MODEL_UNREACHABLE,
        "grounded": result.grounded,
        "ungrounded": result.ungrounded,
        "mode": result.mode,
        "trace": result.trace,
    }
