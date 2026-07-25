"""Validator agent — a second model pass over uncertain work.

Two jobs:

1. **Re-check low-confidence tags.** The first pass sees one transaction in
   isolation. The validator sees the same transaction plus the first pass's
   answer, under a stricter prompt. Agreement raises confidence; a confident
   disagreement replaces the tag; mutual uncertainty sends the row to a human.

2. **Classify money relationships.** For each detected person, read the
   chronological flow and decide whether the user lends, borrows, splits bills,
   is square, or is dealing with a business.

Both write model output into the database, so both sanity-check it first. An
earlier version did not, and persisted a degenerate loop —
`"the account holder is account holder is account holder is…"` — straight onto
a Person row, where it rendered in the UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.llm.client import LLMClient
from app.llm.prompts import (
    RELATIONSHIP_SCHEMA,
    RELATIONSHIP_SYSTEM,
    RELATIONSHIP_USER,
    VALIDATOR_SCHEMA,
    VALIDATOR_SYSTEM,
    VALIDATOR_USER,
)
from app.models import Person, TagSource, Transaction, TxnDirection

logger = logging.getLogger(__name__)

# A disagreement only overrides the first pass if the validator is this sure.
# Below it, both passes are uncertain and a human should look.
_OVERRIDE_CONFIDENCE = 0.85

# Relationship verdicts below this are recorded but not written to Person.notes.
_RELATIONSHIP_MIN_CONFIDENCE = 0.60

# Reclassifying a "friend" as a vendor changes what the People page shows, so it
# needs more certainty than a note does.
_VENDOR_MIN_CONFIDENCE = 0.70

_HISTORY_ROWS = 20


@dataclass
class ValidationDecision:
    txn_id: int
    before: str | None
    after: str | None
    agreed: bool
    confidence: float
    note: str


def looks_degenerate(text: str, *, max_words: int = 60) -> bool:
    """True if `text` shows the repetition patterns that mean a broken generation.

    Deliberately narrow. An earlier version of this check counted repeated
    4-character windows and rejected anything with 8 or more — which every
    English paragraph trips on `" the"` and `"tion"`, so it rejected *all* valid
    prose and the app silently fell back to canned text everywhere. The lesson:
    a quality gate that fires on good output is worse than no gate, because it
    hides the fact that the good path never runs.

    What is actually checked:
      * the same word three times in a row
      * any 3-word phrase repeated 4+ times
    """
    words = text.split()
    if not words or len(words) > max_words * 4:
        return True

    consecutive = 1
    for i in range(1, len(words)):
        if words[i].lower() == words[i - 1].lower():
            consecutive += 1
            if consecutive >= 3:
                return True
        else:
            consecutive = 1

    if len(words) >= 6:
        counts: dict[str, int] = {}
        for i in range(len(words) - 2):
            phrase = " ".join(w.lower() for w in words[i:i + 3])
            counts[phrase] = counts.get(phrase, 0) + 1
            if counts[phrase] >= 4:
                return True

    return False


async def revalidate_low_confidence(
    db: Session,
    *,
    llm: LLMClient,
    threshold: float = 0.70,
    limit: int = 200,
) -> list[ValidationDecision]:
    """Re-check tags the first pass was unsure about."""
    candidates = (
        db.query(Transaction)
        .filter(
            Transaction.tag_source == TagSource.LLM,
            Transaction.tag_confidence < threshold,
            Transaction.is_duplicate.is_(False),
        )
        .order_by(Transaction.tag_confidence.asc())
        .limit(limit)
        .all()
    )
    if not candidates:
        return []

    decisions: list[ValidationDecision] = []
    for txn in candidates:
        original_category = txn.category

        result = await llm.structured(
            [
                {"role": "system", "content": VALIDATOR_SYSTEM},
                {"role": "user", "content": VALIDATOR_USER.format(
                    description=txn.raw_description[:300],
                    merchant=txn.merchant_normalized or "(unknown)",
                    amount=f"{txn.amount:,.2f}",
                    direction=txn.direction.value,
                    source=txn.source.value,
                    category=original_category or "(none)",
                    subcategory=txn.subcategory or "(none)",
                    confidence=f"{txn.tag_confidence or 0:.2f}",
                    reason=(txn.tag_reason or "")[:140],
                )},
            ],
            schema=VALIDATOR_SCHEMA,
        )
        verdict = result.json()
        if verdict is None:
            # No second opinion available. Leave the first pass exactly as it
            # was — do not invent agreement to make the numbers look better.
            logger.debug("Validator unavailable for txn %s: %s", txn.id, result.error)
            continue

        agreed = bool(verdict.get("agree"))
        confidence = min(1.0, max(0.0, float(verdict.get("confidence") or 0.0)))
        note = (verdict.get("reason") or "").strip()[:160]
        proposed = verdict.get("category") or original_category

        if agreed:
            # Two independent passes reaching the same answer is genuine evidence,
            # so take the higher confidence of the two.
            txn.tag_confidence = max(txn.tag_confidence or 0.0, confidence)
            txn.tag_reason = f"{txn.tag_reason or ''} · confirmed: {note}".strip(" ·")
            txn.needs_review = txn.tag_confidence < threshold
        elif confidence >= _OVERRIDE_CONFIDENCE:
            txn.category = proposed
            txn.subcategory = verdict.get("subcategory") or None
            txn.tag_confidence = confidence
            txn.tag_source = TagSource.VALIDATOR
            txn.tag_reason = f"validator overrode '{original_category}': {note}"
            txn.needs_review = False
        else:
            # Disagreement without conviction. A human decides.
            txn.tag_reason = (
                f"{txn.tag_reason or ''} · validator suggested "
                f"'{proposed}' ({note})"
            ).strip(" ·")
            txn.needs_review = True

        decisions.append(ValidationDecision(
            txn_id=txn.id,
            before=original_category,
            after=txn.category,
            agreed=agreed,
            confidence=txn.tag_confidence or 0.0,
            note=note,
        ))

    db.commit()
    return decisions


async def classify_relationships(
    db: Session, *, llm: LLMClient
) -> list[dict]:
    """Work out what kind of money relationship each tracked person represents."""
    people = db.query(Person).filter(Person.relationship_type == "friend").all()
    out: list[dict] = []

    for person in people:
        txns = (
            db.query(Transaction)
            .filter(Transaction.person_id == person.id)
            .order_by(Transaction.posted_at.asc())
            .all()
        )
        if not txns:
            continue

        sent = sum(t.amount for t in txns if t.direction == TxnDirection.DEBIT)
        received = sum(t.amount for t in txns if t.direction == TxnDirection.CREDIT)
        sent_count = sum(1 for t in txns if t.direction == TxnDirection.DEBIT)
        received_count = len(txns) - sent_count

        history = "\n".join(
            f"  {t.posted_at.date()}  "
            f"{'sent to them' if t.direction == TxnDirection.DEBIT else 'received from them'}"
            f"  Rs {t.amount:,.0f}"
            for t in txns[:_HISTORY_ROWS]
        )

        result = await llm.structured(
            [
                {"role": "system", "content": RELATIONSHIP_SYSTEM},
                {"role": "user", "content": RELATIONSHIP_USER.format(
                    name=person.name,
                    sent=f"{sent:,.0f}", sent_count=sent_count,
                    received=f"{received:,.0f}", received_count=received_count,
                    net=f"{sent - received:,.0f}",
                    history=history,
                )},
            ],
            schema=RELATIONSHIP_SCHEMA,
        )
        verdict = result.json()
        if verdict is None:
            continue

        kind = verdict.get("kind") or "settled"
        summary = (verdict.get("summary") or "").strip()
        confidence = min(1.0, max(0.0, float(verdict.get("confidence") or 0.0)))

        # Never write model text to the database without checking it first.
        if summary and looks_degenerate(summary):
            logger.warning(
                "Discarding degenerate relationship summary for person %s", person.id
            )
            summary = ""

        if summary and confidence >= _RELATIONSHIP_MIN_CONFIDENCE:
            person.notes = f"{kind.replace('_', ' ')} — {summary}"
        if kind == "vendor" and confidence >= _VENDOR_MIN_CONFIDENCE:
            person.relationship_type = "vendor"

        out.append({
            "person_id": person.id,
            "name": person.name,
            "kind": kind,
            "net_to_user": round(sent - received, 2),
            "summary": summary,
            "confidence": round(confidence, 2),
        })

    db.commit()
    return out
