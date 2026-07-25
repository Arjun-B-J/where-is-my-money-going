"""Group transactions by counterparty so a user can label a whole batch at once.

Each group surfaces its size, total volume, sample descriptions, and the
canonical UPI/merchant key — meant for the v0.4 review UX where you click
once per recipient and the backend backfills the rest.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Transaction


@dataclass
class RecipientGroup:
    key: str                       # canonical counterparty key (UPI or merchant)
    display_name: str
    sample_descriptions: list[str]
    transaction_ids: list[int]
    total_amount: float
    debit_count: int
    credit_count: int
    common_category: str | None    # most common existing tag, if any


def group_unreviewed(db: Session, limit: int = 30) -> list[RecipientGroup]:
    """Return clusters of similar transactions that still need review."""
    rows = (
        db.query(Transaction)
        .filter(Transaction.needs_review.is_(True))
        .all()
    )

    by_key: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        key = t.counterparty_id or t.merchant_normalized or t.raw_description[:40]
        by_key[key.lower()].append(t)

    groups: list[RecipientGroup] = []
    for key, members in by_key.items():
        from app.models import TxnDirection
        members.sort(key=lambda t: t.posted_at, reverse=True)
        cats = [t.category for t in members if t.category]
        common_cat = max(set(cats), key=cats.count) if cats else None
        debit_count = sum(1 for t in members if t.direction == TxnDirection.DEBIT)
        credit_count = sum(1 for t in members if t.direction == TxnDirection.CREDIT)
        display = (
            members[0].merchant_normalized
            or members[0].counterparty_id
            or members[0].raw_description[:40]
        )
        groups.append(RecipientGroup(
            key=key,
            display_name=display,
            sample_descriptions=[t.raw_description[:120] for t in members[:3]],
            transaction_ids=[t.id for t in members],
            total_amount=round(sum(t.amount for t in members), 2),
            debit_count=debit_count,
            credit_count=credit_count,
            common_category=common_cat,
        ))
    groups.sort(key=lambda g: -len(g.transaction_ids))
    return groups[:limit]
