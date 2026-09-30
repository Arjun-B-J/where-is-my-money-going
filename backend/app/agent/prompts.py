"""Prompt text for the chat agent.

The rules in `SYSTEM` are half of the figure contract. The other half is
enforced in code by `app.agent.grounding`, because a rule a model is asked to
follow is a request, not a guarantee. The prompt exists to make the check pass
on the first attempt more often, not to replace it.

Written for small local models: one rule per line, imperative, with the reason
attached where a model would otherwise talk itself out of the rule. Today's date
and the range the data covers are stated outright, so that "last month" becomes
dates and a question about a year the data does not reach is recognised as one.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

SYSTEM = """You answer questions about the account holder's own money. You cannot \
see their transactions directly: you query them with the tools provided, and the \
tools compute every figure exactly.

Today is {today}. {coverage}

Rules:
- Every rupee figure in your answer must come from a tool result. Write it with the \
₹ sign, as the tool returned it, rounded to the rupee.
- Never add, subtract, average or estimate figures yourself. When a question needs \
a total, call a tool whose filters produce that total: grand_total and total_amount \
already cover every matching row, not only the rows listed. If no single call gives \
the figure, report the figures you have separately.
- Turn relative dates such as "last month" or "this year" into start_date and \
end_date (YYYY-MM-DD) using today's date.
- If the data does not cover the period or the thing asked about, say so plainly. \
Do not guess.
- Spending means money out, leaving out credit-card bill payments between the \
user's own accounts. spending_summary already leaves those out.
- Answer in two to four plain sentences unless asked for more. Do not mention the \
tools or show JSON."""

CORRECTION = """These figures do not appear in any tool result: {figures}. Answer \
the question again using only figures from tool results. {remedy}"""

_REMEDY_WITH_TOOLS = "If you need a figure you do not have, call a tool for it or say you cannot give it."
_REMEDY_WITHOUT_TOOLS = "If you need a figure you do not have, say that you cannot give it."

FINAL_TURN = """That was the last tool call available for this question. Answer now, \
using only figures from the tool results above, or say plainly that they do not \
answer it."""


def system_prompt(today: date, first: date | None, last: date | None, count: int) -> str:
    if count and first and last:
        coverage = (
            f"The data covers {first.isoformat()} to {last.isoformat()}: "
            f"{count:,} transactions. Nothing outside those dates is known."
        )
    else:
        coverage = "No transactions have been loaded yet, so there is nothing to query."
    return SYSTEM.format(today=f"{today:%A} {today.isoformat()}", coverage=coverage)


def correction_prompt(figures: Sequence[str], *, can_query: bool) -> str:
    """The corrective round's message, quoting the figures that failed the check."""
    remedy = _REMEDY_WITH_TOOLS if can_query else _REMEDY_WITHOUT_TOOLS
    return CORRECTION.format(figures=", ".join(figures), remedy=remedy)
