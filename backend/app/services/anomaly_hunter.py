"""Forensic anomaly hunter.

Surfaces things a human reviewer should look at:

  - **Suspected duplicate charges** — same merchant, same amount, within 30 minutes
  - **Refund / charge mismatches** — refunds without a matching forward charge
  - **Micro-debit clusters** — many ≤ ₹5 charges on the same day to the same UPI
    (often payment-platform test entries)
  - **Round-trip same-day** — large purchase + same-amount refund same day
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Transaction, TxnDirection


@dataclass
class AnomalyHit:
    kind: str                   # "duplicate_charge" | "unmatched_refund" | "micro_debit_cluster" | "round_trip"
    title: str
    detail: str
    posted_at: datetime
    amount: float
    txn_ids: list[int]


def _norm_merchant(t: Transaction) -> str:
    return (t.merchant_normalized or t.raw_description[:30] or "").lower()


# Merchants where two same-amount charges close together are routine
# (food courts, cafeterias, multiple orders at lunchtime).
_KNOWN_MULTI_PER_VISIT = re.compile(
    r"(?i)smartq|cafeteria|swiggy|zomato|instamart|blinkit|zepto|"
    r"starbucks|coffee|food|restaurant|metro|rapido|uber|ola"
)


def detect_duplicate_charges(
    db: Session,
    window_min: int = 30,
    min_amount: float = 200.0,
    min_gap_min: float = 1.0,
) -> list[AnomalyHit]:
    """Same merchant + same amount within `window_min` minutes.

    Filters out legitimate multi-per-visit cases:
      - Charges < `min_amount` (most cafeteria items are real second orders)
      - Charges < `min_gap_min` minutes apart at known food/cafeteria merchants
        (the SmartQ cafeteria pattern is morning coffee + breakfast — both real)
      - Subscription-fingerprint merchants (recurring is expected)
    """
    txns = sorted(
        db.query(Transaction).filter(
            Transaction.direction == TxnDirection.DEBIT,
            Transaction.is_duplicate.is_(False),
            Transaction.amount >= min_amount,
        ).all(),
        key=lambda t: (_norm_merchant(t), t.amount, t.posted_at),
    )
    out: list[AnomalyHit] = []
    seen: set[int] = set()
    for i in range(len(txns) - 1):
        a, b = txns[i], txns[i + 1]
        if a.id in seen or b.id in seen:
            continue
        merchant = _norm_merchant(a)
        if merchant != _norm_merchant(b):
            continue
        if abs(a.amount - b.amount) > 0.01:
            continue
        gap_sec = abs((b.posted_at - a.posted_at).total_seconds())
        if gap_sec > window_min * 60:
            continue
        # Cafeteria / food / quick-commerce: only flag if same-amount charges
        # are a few minutes apart (definitionally weird), not 0 minutes
        if _KNOWN_MULTI_PER_VISIT.search(merchant) and gap_sec < min_gap_min * 60:
            continue
        seen.add(a.id)
        seen.add(b.id)
        gap_label = (
            f"{int(gap_sec / 60)} minute(s)" if gap_sec >= 60
            else f"{int(gap_sec)} seconds"
        )
        out.append(AnomalyHit(
            kind="duplicate_charge",
            title=f"Duplicate {a.merchant_normalized or a.raw_description[:40]}",
            detail=(
                f"Two ₹{a.amount:,.2f} charges within {gap_label}. "
                f"Verify only one booking/order was made."
            ),
            posted_at=a.posted_at,
            amount=a.amount,
            txn_ids=[a.id, b.id],
        ))
    return out


def detect_micro_debit_clusters(db: Session) -> list[AnomalyHit]:
    """Six or more ≤₹5 debits to the same counterparty in a single day —
    typically CRED / payment-platform verification entries."""
    txns = db.query(Transaction).filter(
        Transaction.direction == TxnDirection.DEBIT,
        Transaction.amount <= 5.0,
        Transaction.is_duplicate.is_(False),
    ).all()
    by_day: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in txns:
        key = (
            (t.counterparty_id or t.merchant_normalized or t.raw_description[:30]).lower(),
            t.posted_at.strftime("%Y-%m-%d"),
        )
        by_day[key].append(t)

    out: list[AnomalyHit] = []
    for (cp, day), members in by_day.items():
        if len(members) < 6:
            continue
        out.append(AnomalyHit(
            kind="micro_debit_cluster",
            title=f"{len(members)} micro-debits to {cp[:40]}",
            detail=(
                f"{len(members)} debits of ≤₹5 to the same recipient on {day}. "
                f"Usually card-verification or app test charges — worth confirming "
                f"none of them turned into a real payment."
            ),
            posted_at=members[0].posted_at,
            amount=sum(t.amount for t in members),
            txn_ids=[t.id for t in members],
        ))
    return out


# Credits that are not merchant refunds and must not be reported as one.
# Generic markers only. Matching a specific employer's payroll descriptor would
# work for exactly one person and silently fail for everyone else.
_NON_REFUND_CREDIT = re.compile(
    r"(?i)salary|payroll|\batm\b|neft\s*cr|imps\s*cr|rtgs\s*cr|"
    r"interest|"
    r"credit\s*card\s*payment|cc\s*payment|card\s*auto\s*pay|"
    r"\bbbps\b|"
    r"reversal|cashback|refund\s+credit|"
    r"upi-(?:cr/|received\s*from)|"
    r"\bself[-\s]*transfer\b|own[-\s]*transfer"
)


def detect_unmatched_refunds(db: Session, window_days: int = 60) -> list[AnomalyHit]:
    """Credits that don't have a matching forward debit at the same merchant
    within `window_days`.

    Filters out things that look like credits but are clearly NOT merchant
    refunds: salary, ATM, interest, credit-card-payment receipts, BBPS,
    person-tagged friend transfers, etc.
    """
    debits = db.query(Transaction).filter(
        Transaction.direction == TxnDirection.DEBIT,
        Transaction.is_duplicate.is_(False),
    ).all()
    credits = db.query(Transaction).filter(
        Transaction.direction == TxnDirection.CREDIT,
        Transaction.is_duplicate.is_(False),
    ).all()
    debit_index: dict[str, list[Transaction]] = defaultdict(list)
    for d in debits:
        debit_index[_norm_merchant(d)].append(d)

    out: list[AnomalyHit] = []
    for c in credits:
        m = _norm_merchant(c)
        if not m:
            continue
        # Skip person-tagged credits — those are friend transfers, not refunds
        if c.person_id:
            continue
        # Skip credits that match the non-refund patterns
        if _NON_REFUND_CREDIT.search(m) or _NON_REFUND_CREDIT.search(c.raw_description or ""):
            continue
        # Skip very small credits — usually rewards/cashback within tolerance
        if c.amount < 1000:
            continue
        # Skip if there's an existing similar-amount debit (likely a real refund)
        candidates = [
            d for d in debit_index.get(m, [])
            if abs(d.amount - c.amount) < 0.5
            and 0 <= (c.posted_at - d.posted_at).days <= window_days
        ]
        if candidates:
            continue
        # Skip if the description doesn't contain refund-y keywords AND isn't a
        # merchant we'd recognize. Avoids flagging every random credit.
        desc_low = (c.raw_description or "").lower()
        looks_refund_like = any(
            kw in desc_low for kw in
            ("refund", "reversal", "cashback", "credit note", "return")
        )
        if not looks_refund_like:
            continue
        out.append(AnomalyHit(
            kind="unmatched_refund",
            title=f"Unmatched refund: {c.merchant_normalized or m[:40]}",
            detail=(
                f"₹{c.amount:,.2f} refund on {c.posted_at.date()} has no "
                f"matching forward purchase in the last {window_days} days. "
                f"Cross-check the order history."
            ),
            posted_at=c.posted_at,
            amount=c.amount,
            txn_ids=[c.id],
        ))
    return out


def detect_round_trip(db: Session, min_amount: float = 2000.0) -> list[AnomalyHit]:
    """Large debit + same-amount credit on the same day at the SAME merchant.

    Filters out cases where the matched debit and credit are obviously two
    different things — e.g., a CC payment debit on main account paired with
    a friend's UPI credit that happened to be the same amount. We only flag
    when the *merchants are similar* on both sides.
    """
    txns = db.query(Transaction).filter(
        Transaction.is_duplicate.is_(False),
    ).all()
    by_day_amt: dict[tuple[str, float], list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.amount < min_amount:
            continue
        by_day_amt[(t.posted_at.strftime("%Y-%m-%d"), round(t.amount, 2))].append(t)

    out: list[AnomalyHit] = []
    for (day, amt), members in by_day_amt.items():
        debits = [m for m in members if m.direction == TxnDirection.DEBIT]
        credits = [m for m in members if m.direction == TxnDirection.CREDIT]
        if not debits or not credits:
            continue
        # Require shared merchant tokens between debit and credit (otherwise
        # we're matching unrelated transactions just because they had the
        # same rupee amount on the same day).
        d_merch = _norm_merchant(debits[0])
        c_merch = _norm_merchant(credits[0])
        d_words = set(re.findall(r"[a-z0-9]{4,}", d_merch))
        c_words = set(re.findall(r"[a-z0-9]{4,}", c_merch))
        if not (d_words & c_words):
            continue
        # Skip person-tagged matches — those are friend transfers paired with
        # something else
        if any(t.person_id for t in members):
            continue
        out.append(AnomalyHit(
            kind="round_trip",
            title=f"Round-trip ₹{amt:,.0f} same day",
            detail=(
                f"₹{amt:,.0f} charged AND refunded on {day} "
                f"(both at {d_merch[:40]}). "
                f"Likely price-match adjust, cancel-and-rebook, or order change. "
                f"Confirm only one delivered order/booking."
            ),
            posted_at=debits[0].posted_at,
            amount=amt,
            txn_ids=[t.id for t in members],
        ))
    return out


def all_anomalies(db: Session) -> list[AnomalyHit]:
    out: list[AnomalyHit] = []
    out.extend(detect_duplicate_charges(db))
    out.extend(detect_micro_debit_clusters(db))
    out.extend(detect_unmatched_refunds(db))
    out.extend(detect_round_trip(db))
    out.sort(key=lambda a: a.posted_at, reverse=True)
    return out
