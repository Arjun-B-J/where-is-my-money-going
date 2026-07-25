"""Chat over your own spending.

The model is given an aggregate summary, not the transaction table. That keeps
the prompt small, but the real reason is that a question like "how much did I
spend on food" is answered from totals anyway — and handing a model 900 raw rows
including every payee's name is more exposure than the feature needs.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.client import LLMUnavailableError, get_llm
from app.llm.prompts import CHAT_SYSTEM
from app.models import Person, Transaction, TxnDirection
from app.money import rupees
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

_TOP_N = 8


def _summary_for_prompt(db: Session) -> str:
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


def _messages(db: Session, request: ChatRequest) -> list[dict]:
    return [
        {"role": "system", "content": f"{CHAT_SYSTEM}\n\n{_summary_for_prompt(db)}"},
        *({"role": m.role, "content": m.content} for m in request.messages),
    ]


@router.post("/stream")
async def chat_stream(
    request: ChatRequest, db: Session = Depends(get_db)
) -> StreamingResponse:
    """Server-sent events: `{"delta": "..."}` chunks, then `{"done": true}`.

    A model failure emits an `error` event. It does not emit apologetic prose
    pretending to be an answer.
    """
    llm = get_llm()
    messages = _messages(db, request)

    async def events():
        try:
            async for chunk in llm.stream(messages, temperature=0.4):
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except LLMUnavailableError as e:
            logger.warning("Chat stream failed: %s", e)
            yield f"data: {json.dumps({'error': 'The local model is not reachable.'})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("")
async def chat(request: ChatRequest, db: Session = Depends(get_db)) -> dict:
    """Non-streaming variant."""
    result = await get_llm().complete(_messages(db, request), temperature=0.4)
    if result.failed:
        return {"ok": False, "error": "The local model is not reachable.", "reply": None}
    return {"ok": True, "reply": result.text, "error": None}
