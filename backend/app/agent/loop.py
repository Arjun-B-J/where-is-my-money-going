"""The chat agent: the model picks the queries, code computes every figure.

DECISIONS.md §15 chose tool-calling over the database for chat, because the
questions people ask about money are filters and aggregates with exact answers.
This module runs that conversation.

1. The system prompt states today's date and the range the data covers, so
   "last month" can be turned into dates and a question about a year the data
   does not reach can be recognised as one.
2. The model asks for tools, `app.agent.tools` runs them and the results go
   back. At most five rounds of that, then one last call with no tools offered,
   so a model that keeps asking cannot loop for ever.
3. Every rupee figure in the answer is checked against the numbers the tools
   returned (`app.agent.grounding`). If any is missing, the model gets one
   corrective round. If the figures still do not match, the answer goes out with
   `grounded=False` and the figures listed, and the UI says so.

A model that fails produces `ok=False` and no text. There is no apologetic
filler standing in for an answer, for the reason given in DECISIONS.md §4.

The chat this replaced, a fixed summary pasted into the prompt, is kept as
`run_summary`. It is the baseline the chat eval compares against, and what a
model that cannot call tools gets instead, labelled `mode="summary"` so the UI
can say that the answer came from a fixed summary rather than from queries.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.agent.grounding import check, numbers_in_json, numbers_in_text
from app.agent.prompts import FINAL_TURN, correction_prompt, system_prompt
from app.agent.tools import TOOL_SCHEMAS, data_range, execute, parse_arguments
from app.llm.client import LLMClient
from app.llm.prompts import CHAT_SYSTEM
from app.models import Person, Transaction, TxnDirection
from app.money import rupees

logger = logging.getLogger(__name__)

# Room for an overview, a summary and a drill-down, plus a retry after a
# rejected argument. A model still asking for tools after five rounds is
# looping, not researching.
MAX_TOOL_ROUNDS = 5

# Chat messages as the routes and the eval pass them: {"role", "content"}.
History = Sequence[Mapping[str, str]]


@dataclass(frozen=True)
class ToolStep:
    """One tool call, as the chat UI shows it and the trace records it."""

    tool: str
    args: dict[str, Any]
    ok: bool
    ms: int

    def as_trace(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "ok": self.ok, "ms": self.ms}


@dataclass
class AgentResult:
    """How one question was answered.

    `ok=False` means there is no answer: `text` is empty and `error` says why.
    `grounded=False` means there is an answer and `ungrounded` lists the figures
    in it that nothing computed. Neither state is shown as an ordinary answer.
    """

    ok: bool
    text: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    grounded: bool = False
    ungrounded: list[str] = field(default_factory=list)
    error: str | None = None
    # "agent" when queries answered it; "summary" when a model that cannot call
    # tools was handed the old fixed summary instead.
    mode: str = "agent"
    corrective_round: bool = False
    model_calls: int = 0


def supports_tools(llm: object) -> bool:
    return callable(getattr(llm, "with_tools", None))


def cannot_call_tools(error: str | None) -> bool:
    """Whether a failed call was Ollama refusing tools for this model.

    It answers HTTP 400 "<model> does not support tools" when the model's
    template has no tool support. That is a property of the model, not an
    outage, and it deserves a different response from a daemon that is down.
    """
    return bool(error) and "does not support tools" in str(error).lower()


def _numbers_from_user(history: History) -> list[float]:
    """Figures the user wrote. Repeating them back is not inventing them."""
    return [
        value
        for message in history if message.get("role") == "user"
        for value in numbers_in_text(message.get("content", ""))
    ]


def _call_parts(call: Mapping[str, Any]) -> tuple[str, Any]:
    """A tool call's name and raw arguments, from Ollama's shape or a flat one."""
    function = call.get("function")
    source = function if isinstance(function, Mapping) else call
    return str(source.get("name") or ""), source.get("arguments")


def _echo(content: str, calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The assistant turn that asked for tools, as it goes back into the history.

    Arguments go back as objects even when the model wrote a JSON string:
    Ollama decodes them into a map and would reject the whole request.
    """
    echoed: list[dict[str, Any]] = []
    for call in calls:
        name, raw = _call_parts(call)
        function = call.get("function")
        extras = dict(function) if isinstance(function, Mapping) else {}
        echoed.append({
            **call,
            "function": {**extras, "name": name, "arguments": parse_arguments(raw) or {}},
        })
    return {"role": "assistant", "content": content, "tool_calls": echoed}


def _run(
    db: Session, call: Mapping[str, Any], *, redact: bool
) -> tuple[ToolStep, dict[str, Any]]:
    name, raw = _call_parts(call)
    started = time.perf_counter()
    result = execute(db, name, raw, redact=redact)
    step = ToolStep(
        tool=name or "(unnamed)",
        args=parse_arguments(raw) or {},
        ok="error" not in result,
        ms=int((time.perf_counter() - started) * 1000),
    )
    return step, result


def _tool_message(call: Mapping[str, Any], name: str, result: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "tool",
        "tool_name": name,
        "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
    }
    # Newer Ollama versions give each call an id; echoing it pairs the result
    # with its call when a turn asked for several. Older ones ignore the field.
    if call.get("id"):
        message["tool_call_id"] = call["id"]
    return message


async def stream_agent(
    llm: LLMClient,
    db: Session,
    history: History,
    *,
    today: date | None = None,
    redact: bool = False,
) -> AsyncGenerator[ToolStep | AgentResult, None]:
    """Answer the last question in `history`, yielding each tool call as it runs.

    The final item is always an `AgentResult`. `today` is injectable so the eval
    can pin relative dates; by default it is the local calendar date, because
    "today" is the user's today and in India UTC is still yesterday until 05:30.
    """
    first, last, count = data_range(db)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(today or date.today(), first, last, count)},
        *({"role": message["role"], "content": message["content"]} for message in history),
    ]
    known = _numbers_from_user(history)
    trace: list[dict[str, Any]] = []
    rounds_left = MAX_TOOL_ROUNDS
    calls = 0
    # The answer that failed the check and the figures that failed it. Set
    # during the corrective round, of which there is only ever one.
    rejected: tuple[str, list[str]] | None = None

    while True:
        offered = TOOL_SCHEMAS if rounds_left > 0 else []
        turn = await llm.with_tools(messages, offered)
        calls += 1

        if turn.ok and turn.tool_calls and offered:
            rounds_left -= 1
            messages.append(_echo(turn.content, turn.tool_calls))
            for call in turn.tool_calls:
                step, result = _run(db, call, redact=redact)
                trace.append(step.as_trace())
                known.extend(numbers_in_json(result))
                messages.append(_tool_message(call, step.tool, result))
                yield step
            if rounds_left == 0:
                messages.append({"role": "user", "content": FINAL_TURN})
            continue

        text = turn.content.strip() if turn.ok else ""
        if not text:
            error = turn.error or "the model asked for more tools after the last round"
            if rejected is None:
                yield AgentResult(ok=False, trace=trace, error=error, model_calls=calls)
            else:
                # The correction failed, not the answer. The first answer is
                # real and goes out with its flag, rather than being thrown away.
                yield AgentResult(
                    ok=True, text=rejected[0], trace=trace, grounded=False,
                    ungrounded=rejected[1], error=error, corrective_round=True,
                    model_calls=calls,
                )
            return

        report = check(text, known)
        if report.grounded or rejected is not None:
            yield AgentResult(
                ok=True, text=text, trace=trace, grounded=report.grounded,
                ungrounded=report.ungrounded, corrective_round=rejected is not None,
                model_calls=calls,
            )
            return

        rejected = (text, report.ungrounded)
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": correction_prompt(report.ungrounded, can_query=rounds_left > 0),
        })


async def _final(items: AsyncIterator[ToolStep | AgentResult]) -> AgentResult:
    result: AgentResult | None = None
    async for item in items:
        if isinstance(item, AgentResult):
            result = item
    if result is None:
        raise RuntimeError("the agent finished without a result")
    return result


async def run_agent(
    llm: LLMClient,
    db: Session,
    history: History,
    *,
    today: date | None = None,
    redact: bool = False,
) -> AgentResult:
    """The agent alone, with no fallback. The chat eval's `agent` arm."""
    return await _final(stream_agent(llm, db, history, today=today, redact=redact))


async def stream_answer(
    llm: LLMClient,
    db: Session,
    history: History,
    *,
    today: date | None = None,
    redact: bool = False,
) -> AsyncGenerator[ToolStep | AgentResult, None]:
    """What the chat routes use: the agent, or the fixed summary if the model cannot call tools.

    The fallback is labelled rather than silent. An answer from the summary can
    only quote the handful of totals the summary carries, and the user should
    know that before trusting it.
    """
    if not supports_tools(llm):
        yield await run_summary(llm, db, history)
        return
    async with aclosing(stream_agent(llm, db, history, today=today, redact=redact)) as items:
        async for item in items:
            if (
                isinstance(item, AgentResult) and not item.ok and not item.trace
                and cannot_call_tools(item.error)
            ):
                logger.info("%s cannot call tools; answering from the fixed summary",
                            getattr(llm, "model", "the model"))
                yield await run_summary(llm, db, history)
                return
            yield item


async def answer(
    llm: LLMClient,
    db: Session,
    history: History,
    *,
    today: date | None = None,
    redact: bool = False,
) -> AgentResult:
    """`stream_answer` without the stream, for the non-streaming route."""
    return await _final(stream_answer(llm, db, history, today=today, redact=redact))


# ---------------------------------------------------------------------------
# The fixed-summary chat this replaced
# ---------------------------------------------------------------------------
# Kept exactly as it was, because it is the baseline: the eval's `summary` arm
# measures what the agent is an improvement over, and a baseline quietly
# improved while being kept would flatter nothing but the comparison.

_TOP_N = 8


def summary_for_prompt(db: Session) -> str:
    """A compact, factual snapshot of the user's data."""
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()
    if not txns:
        return "The user has not ingested any transactions yet."

    spent = sum(t.amount for t in txns if t.direction == TxnDirection.DEBIT)
    received = sum(t.amount for t in txns if t.direction == TxnDirection.CREDIT)

    categories: dict[str, float] = defaultdict(float)
    payees: dict[str, float] = defaultdict(float)
    for txn in txns:
        if txn.direction != TxnDirection.DEBIT:
            continue
        categories[txn.category or "uncategorized"] += txn.amount
        payees[(txn.merchant_normalized or txn.raw_description)[:40]] += txn.amount

    def top(values: dict[str, float]) -> str:
        ranked = sorted(values.items(), key=lambda kv: -kv[1])[:_TOP_N]
        return "\n".join(f"  - {name}: {rupees(total)}" for name, total in ranked) or "  (none)"

    people_lines: list[str] = []
    for person in db.query(Person).all():
        rows = db.query(Transaction).filter(Transaction.person_id == person.id).all()
        if not rows:
            continue
        net = sum(
            row.amount if row.direction == TxnDirection.DEBIT else -row.amount
            for row in rows
        )
        side = "they are behind" if net > 0 else "the user is behind"
        people_lines.append(f"  - {person.name}: {rupees(abs(net))}, {side}")

    period = f"{min(t.posted_at for t in txns):%b %Y} to {max(t.posted_at for t in txns):%b %Y}"
    return f"""DATA SUMMARY ({len(txns)} transactions, {period})
Total spent: {rupees(spent)}
Total received: {rupees(received)}
Net: {rupees(received - spent)}

Spending by category:
{top(categories)}

Largest payees:
{top(payees)}

People:
{chr(10).join(people_lines) or "  (none tracked)"}"""


async def run_summary(llm: LLMClient, db: Session, history: History) -> AgentResult:
    """Answer from the fixed summary, as chat did before the agent.

    Its answer is checked the same way, against the numbers in the summary it
    was given, so the eval can compare how often each approach states a figure
    that its own input does not contain.
    """
    summary = summary_for_prompt(db)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"{CHAT_SYSTEM}\n\n{summary}"},
        *({"role": message["role"], "content": message["content"]} for message in history),
    ]
    result = await llm.complete(messages, temperature=0.4)
    if result.failed:
        return AgentResult(ok=False, error=result.error, mode="summary", model_calls=1)
    report = check(result.text, [*numbers_in_text(summary), *_numbers_from_user(history)])
    return AgentResult(
        ok=True, text=result.text, grounded=report.grounded, ungrounded=report.ungrounded,
        mode="summary", model_calls=1,
    )
