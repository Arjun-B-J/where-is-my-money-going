"""Checks that only work when you hold two statements at once.

Paying a credit-card bill from a bank account produces two rows in two
documents: a debit on the bank statement and a credit on the card statement.
They should agree. When they do not, either a payment was made from somewhere
else, the two statements cut their billing periods differently, or a parser is
wrong — and all three are worth knowing.

The second check looks for the same purchase appearing on two different
accounts, which happens when a card is linked to a wallet that also exports
its own history.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Transaction, TxnDirection, TxnSource

# Bank-side descriptions that mean "I paid a card bill".
CARD_PAYMENT_MARKERS = (
    "credit card payment", "cc payment", "credit card auto pay", "card auto pay",
    "cred.club", "credclub",
)


def is_internal_transfer(txn: Transaction) -> bool:
    """True if this debit only moves money between accounts the user owns.

    Paying a credit-card bill from a bank account is the common case, and both
    sides of it are in the dataset: the bank debit, and the card purchases it
    settles. Summing every debit therefore counts card spending twice.

    Matched on the description rather than on `subcategory`, because in LLM-first
    mode the rule engine never runs and the subcategory is whatever free text the
    model produced. The description is what the bank actually printed.
    """
    if txn.direction != TxnDirection.DEBIT or txn.source not in _BANK_SOURCES:
        return False
    description = (txn.raw_description or "").lower()
    return any(marker in description for marker in CARD_PAYMENT_MARKERS)

# Two monthly figures within this many rupees are treated as agreeing; statement
# cut-off dates rarely line up to the paisa.
_RECONCILE_TOLERANCE = 100.0

# Below this word overlap, two same-amount transactions are a coincidence.
_MIN_DESCRIPTION_SIMILARITY = 0.15

_BANK_SOURCES = {TxnSource.BANK, TxnSource.BANK_SECONDARY}
_CARD_SOURCES = {TxnSource.CARD, TxnSource.CARD_SECONDARY}


@dataclass
class CCReconcileRow:
    month: str
    bank_side_payment: float        # card-bill debits seen on bank statements
    card_side_billed: float         # spending charged to the card that month
    card_side_payment: float        # payments the card statement recorded receiving
    delta: float                    # bank_side_payment - card_side_payment
    note: str


@dataclass
class PossibleDuplicate:
    txn1_id: int
    txn1_source: str
    txn2_id: int
    txn2_source: str
    amount: float
    days_apart: int
    description_similarity: float



def reconcile_credit_card_payments(db: Session) -> list[CCReconcileRow]:
    """Month by month, compare card-bill payments as seen from both sides."""
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()

    bank_side: dict[str, float] = defaultdict(float)
    card_billed: dict[str, float] = defaultdict(float)
    card_received: dict[str, float] = defaultdict(float)

    for txn in txns:
        month = txn.posted_at.strftime("%Y-%m")

        if txn.source in _BANK_SOURCES and txn.direction == TxnDirection.DEBIT:
            description = (txn.raw_description or "").lower()
            if any(marker in description for marker in CARD_PAYMENT_MARKERS):
                bank_side[month] += txn.amount

        elif txn.source in _CARD_SOURCES:
            if txn.direction == TxnDirection.DEBIT:
                card_billed[month] += txn.amount
            else:
                card_received[month] += txn.amount

    months = sorted(set(bank_side) | set(card_billed) | set(card_received))
    rows: list[CCReconcileRow] = []
    for month in months:
        paid_out = bank_side.get(month, 0.0)
        billed = card_billed.get(month, 0.0)
        received = card_received.get(month, 0.0)
        delta = round(paid_out - received, 2)

        if abs(delta) < _RECONCILE_TOLERANCE:
            note = "Matches"
        elif paid_out == 0 and received > 0:
            note = "Card recorded a payment with no matching bank debit — paid from elsewhere?"
        elif paid_out > 0 and received == 0:
            note = "Bank paid but the card has not recorded it — most likely a timing gap"
        else:
            note = f"Differs by Rs {abs(delta):,.0f}"

        rows.append(CCReconcileRow(
            month=month,
            bank_side_payment=round(paid_out, 2),
            card_side_billed=round(billed, 2),
            card_side_payment=round(received, 2),
            delta=delta,
            note=note,
        ))
    return rows


def find_possible_duplicates(db: Session, day_tolerance: int = 2) -> list[PossibleDuplicate]:
    """Find the same purchase recorded on two different accounts.

    Same amount, within a couple of days, and describing the same thing. Grouping
    by amount first keeps this near-linear on real data — comparing every debit
    against every other would be quadratic over thousands of rows, and identical
    amounts are rare enough that the groups stay tiny.
    """
    txns = db.query(Transaction).filter(
        Transaction.is_duplicate.is_(False),
        Transaction.direction == TxnDirection.DEBIT,
    ).all()

    by_amount: dict[float, list[Transaction]] = defaultdict(list)
    for txn in txns:
        by_amount[round(txn.amount, 2)].append(txn)

    found: list[PossibleDuplicate] = []
    for amount, group in by_amount.items():
        if len(group) < 2:
            continue
        for i, first in enumerate(group):
            for second in group[i + 1:]:
                if first.source == second.source:
                    # Same statement listing it twice is a genuine repeat
                    # purchase, not a cross-source duplicate.
                    continue
                days_apart = abs((first.posted_at - second.posted_at).days)
                if days_apart > day_tolerance:
                    continue
                similarity = _word_overlap(first.raw_description, second.raw_description)
                if similarity < _MIN_DESCRIPTION_SIMILARITY:
                    continue
                found.append(PossibleDuplicate(
                    txn1_id=first.id, txn1_source=first.source.value,
                    txn2_id=second.id, txn2_source=second.source.value,
                    amount=amount, days_apart=days_apart,
                    description_similarity=round(similarity, 2),
                ))

    found.sort(key=lambda hit: -hit.amount)
    return found


def _word_overlap(first: str | None, second: str | None) -> float:
    """Jaccard similarity over words of 3+ characters."""
    if not first or not second:
        return 0.0
    left = {word for word in first.upper().split() if len(word) >= 3}
    right = {word for word in second.upper().split() if len(word) >= 3}
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
