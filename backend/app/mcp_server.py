"""A Model Context Protocol server over the chat agent's read-only tools.

Any MCP client (Claude Desktop, an IDE, another agent) can ask exact questions
of the ledger through the same five tools the in-app chat uses, with the same
argument validation and the same totals. See `app.agent.tools`.

Two decisions shape it.

**People's names are always reduced to initials.** The in-app chat talks to a
model on this machine. An MCP client may not: Claude Desktop, for one, sends
tool results to a cloud model. So every call runs with `redact=True`, and there
is deliberately no switch to turn that off. Amounts, dates and merchant names
still leave, because no spending question can be answered without them;
docs/PRIVACY.md states that boundary, so connecting a client is a choice made
knowingly rather than a leak.

**No SDK.** What this server needs from the protocol is small: JSON-RPC 2.0 over
stdin and stdout, one message per line, an `initialize` handshake, `tools/list`
and `tools/call`. It is written out here directly instead of adding a
dependency, for the reason pyproject.toml gives: every dependency is
supply-chain surface. Everything written to stdout is protocol, so logging goes
to stderr only.

    wimmg mcp
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from app import db as db_module
from app.agent.tools import TOOLS, execute
from app.config import APP_SLUG, APP_VERSION

logger = logging.getLogger(__name__)

# Protocol revisions this server has been written against. A client asking for
# one of them gets it back; anything else gets the newest, as the spec allows,
# and the client decides whether it can continue.
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_INSTRUCTIONS = (
    "Read-only access to the user's own categorised bank and card transactions. "
    "Every figure is computed by a database query, so quote tool results rather than "
    "estimating. People are shown by their initials."
)

# JSON-RPC error codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def tool_listing() -> list[dict[str, Any]]:
    """Every tool with its full JSON Schema.

    MCP accepts a complete schema, unlike Ollama, which keeps only type,
    description and enum per property, so the pydantic schema goes through
    unflattened: bounds, formats and enums all reach the client.
    """
    return [
        {"name": tool.name, "description": tool.description,
         "inputSchema": tool.args.model_json_schema()}
        for tool in TOOLS.values()
    ]


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    with db_module.SessionLocal() as session:
        output = execute(session, str(name), params.get("arguments") or {}, redact=True)
    # A tool-level problem (bad arguments, unknown category) is a result the
    # client's model can act on, so it comes back as isError, not a protocol error.
    return {
        "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
        "structuredContent": output,
        "isError": "error" in output,
    }


def handle(message: Any) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. Notifications get no answer (None)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, _INVALID_REQUEST, "expected a JSON-RPC 2.0 object")
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        # `notifications/initialized` and friends: nothing to say back.
        return None

    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, _INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": APP_SLUG, "version": APP_VERSION},
            "instructions": _INSTRUCTIONS,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_listing()})
    if method == "tools/call":
        if params.get("name") not in TOOLS:
            return _error(request_id, _INVALID_PARAMS, f"unknown tool {params.get('name')!r}")
        return _result(request_id, _call_tool(params))
    return _error(request_id, _METHOD_NOT_FOUND, f"method {method!r} is not supported")


def _replies(line: str) -> Iterable[dict[str, Any]]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        yield _error(None, _PARSE_ERROR, "not valid JSON")
        return
    # Older protocol revisions allowed batches; answering them costs one loop.
    for item in message if isinstance(message, list) else [message]:
        reply = handle(item)
        if reply is not None:
            yield reply


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Read requests from stdin until it closes, one JSON message per line."""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    logger.info("MCP server ready: %d tools, names redacted", len(TOOLS))
    for line in source:
        if not line.strip():
            continue
        for reply in _replies(line):
            # Messages must not contain raw newlines: one line per message.
            sink.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sink.flush()
