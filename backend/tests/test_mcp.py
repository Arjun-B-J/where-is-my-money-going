"""The MCP server: handshake, tool listing, calls, redaction, and the wire format.

The server talks JSON-RPC over stdio with no SDK, so these tests pin the protocol
behaviour a client depends on: ids echoed back, notifications left unanswered,
tool mistakes returned as results the client's model can read, and nothing but
one JSON message per line on stdout.
"""
from __future__ import annotations

import io
import json
from datetime import datetime

from app.mcp_server import SUPPORTED_VERSIONS, handle, serve, tool_listing
from app.models import Person, Transaction, TxnDirection, TxnSource


def _request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_initialize_echoes_a_supported_version():
    reply = handle(_request("initialize", {"protocolVersion": "2025-03-26"}))
    assert reply["id"] == 1
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}


def test_initialize_offers_the_newest_version_for_an_unknown_one():
    reply = handle(_request("initialize", {"protocolVersion": "1999-01-01"}))
    assert reply["result"]["protocolVersion"] == SUPPORTED_VERSIONS[0]


def test_notifications_get_no_reply():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_are_listed_with_full_schemas():
    names = {tool["name"] for tool in tool_listing()}
    assert names == {
        "data_overview", "spending_summary", "find_transactions",
        "people_balances", "recurring_payments",
    }
    summary = next(t for t in tool_listing() if t["name"] == "spending_summary")
    assert summary["inputSchema"]["type"] == "object"
    assert "group_by" in summary["inputSchema"]["properties"]


def test_unknown_method_and_unknown_tool_are_protocol_errors():
    assert handle(_request("resources/list"))["error"]["code"] == -32601
    reply = handle(_request("tools/call", {"name": "drop_table", "arguments": {}}))
    assert reply["error"]["code"] == -32602


def test_bad_arguments_come_back_as_a_readable_tool_error(db):
    """The client's model can fix its call only if it is told what was wrong."""
    reply = handle(_request("tools/call", {
        "name": "spending_summary", "arguments": {"group_by": "planet"},
    }))
    assert reply["result"]["isError"] is True
    assert "invalid arguments" in reply["result"]["content"][0]["text"]


def test_people_are_reduced_to_initials_for_every_client(db):
    """An MCP client may forward results to a cloud model; names never leave."""
    person = Person(name="LIONEL MESSI")
    db.add(person)
    db.flush()
    for direction, amount in ((TxnDirection.DEBIT, 5_000.0), (TxnDirection.CREDIT, 2_000.0)):
        db.add(Transaction(
            external_id=f"x-{direction}", posted_at=datetime(2026, 8, 1, 12), amount=amount,
            direction=direction, source=TxnSource.UPI,
            raw_description="UPI-LIONEL MESSI-lionelmessi@okbank-LOAN",
            merchant_normalized="LIONEL MESSI", counterparty_id="lionelmessi@okbank",
            person_id=person.id,
        ))
    db.commit()

    reply = handle(_request("tools/call", {"name": "people_balances", "arguments": {}}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is False
    assert "MESSI" not in text
    assert "L. M." in text


def test_serve_writes_one_json_message_per_line():
    stdin = io.StringIO(
        json.dumps(_request("initialize", {"protocolVersion": "2025-06-18"})) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + "\n"
        + "{not json\n"
        + json.dumps(_request("ping", request_id=7)) + "\n"
    )
    stdout = io.StringIO()
    serve(stdin, stdout)

    lines = stdout.getvalue().splitlines()
    replies = [json.loads(line) for line in lines]
    assert [r.get("id") for r in replies] == [1, None, 7]
    assert replies[1]["error"]["code"] == -32700
    assert replies[2]["result"] == {}
