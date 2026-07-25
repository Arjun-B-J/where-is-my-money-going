"""Cross-pattern detection over the transaction store.

Detects:
  1. Recurring same-amount payments (rent, subscriptions, salary, etc.)
  2. Friend money loops — paired debits + credits with the same counterparty
  3. Spending anomalies — months where a category spikes 2σ above baseline
  4. Top counterparties (UPI IDs / merchants by frequency)

All deterministic — no LLM. Fast enough to run on every dashboard load.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, stdev

from sqlalchemy.orm import Session

from app.models import Transaction, TxnDirection

# ---- helpers ----

def _norm_counterparty(t: Transaction) -> str | None:
    """Best-effort canonical key for grouping by recipient."""
    if t.counterparty_id:
        return t.counterparty_id.lower()
    if t.merchant_normalized:
        return t.merchant_normalized.lower()
    return None


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


# ---- output shapes ----


@dataclass
class RecurringPayment:
    counterparty: str
    amount: float
    frequency: str            # "monthly" | "weekly" | "irregular"
    occurrences: int
    direction: str            # "debit" | "credit"
    last_seen: datetime
    likely_purpose: str       # rough guess: rent, subscription, salary, ...


@dataclass
class FriendLoop:
    counterparty: str          # name or UPI id
    sent: float
    received: float
    net: float                 # +ve => they owe you
    sent_count: int
    received_count: int
    last_activity: datetime


@dataclass
class CategoryAnomaly:
    category: str
    month: str
    amount: float
    baseline: float
    z_score: float


@dataclass
class TopCounterparty:
    counterparty: str
    total: float
    count: int
    direction: str             # "debit" | "credit"


# ---- detectors ----


def detect_recurring(db: Session, tolerance: float = 0.05) -> list[RecurringPayment]:
    """A counterparty + amount combo that repeats at least 3 times within
    a tight tolerance is flagged as recurring. Cadence is inferred from gaps.
    """
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()

    # Group by (counterparty, ~amount-bucket, direction)
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in txns:
        cp = _norm_counterparty(t)
        if not cp:
            continue
        # bucket amount by 5% tolerance
        bucket = round(t.amount / max(t.amount * tolerance, 1.0))
        groups[(cp, bucket, t.direction.value)].append(t)

    out: list[RecurringPayment] = []
    for (cp, _bucket, direction), members in groups.items():
        if len(members) < 3:
            continue
        members.sort(key=lambda x: x.posted_at)
        # Compute gaps in days
        gaps = [
            (members[i].posted_at - members[i - 1].posted_at).days
            for i in range(1, len(members))
        ]
        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        if 25 <= avg_gap <= 35:
            freq = "monthly"
        elif 6 <= avg_gap <= 9:
            freq = "weekly"
        else:
            freq = "irregular"
        median_amount = sorted(t.amount for t in members)[len(members) // 2]
        purpose = _guess_purpose(members[0])
        out.append(RecurringPayment(
            counterparty=members[0].merchant_normalized or cp,
            amount=round(median_amount, 2),
            frequency=freq,
            occurrences=len(members),
            direction=direction,
            last_seen=members[-1].posted_at,
            likely_purpose=purpose,
        ))
    out.sort(key=lambda r: (r.frequency != "monthly", -r.amount))
    return out


def _guess_purpose(t: Transaction) -> str:
    cat = (t.category or "").lower()
    desc = (t.raw_description or "").lower()
    if cat == "rent" or "rent" in desc:
        return "rent"
    if cat == "salary":
        return "salary"
    if cat == "investments":
        return "SIP / investment"
    if cat == "subscriptions":
        return "subscription"
    if cat == "utilities":
        return "utility bill"
    return "recurring transfer"


def detect_friend_loops(db: Session) -> list[FriendLoop]:
    """A friend loop is a counterparty you both send to AND receive from.
    Net is the difference. Useful for catching IOU patterns the rule engine missed.
    """
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()
    by_cp: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        cp = _norm_counterparty(t)
        if cp:
            by_cp[cp].append(t)

    loops: list[FriendLoop] = []
    for cp, members in by_cp.items():
        debits = [t for t in members if t.direction == TxnDirection.DEBIT]
        credits = [t for t in members if t.direction == TxnDirection.CREDIT]
        if not debits or not credits:
            continue
        sent = sum(t.amount for t in debits)
        received = sum(t.amount for t in credits)
        # Filter to actual friend-like volumes (skip merchant refunds < ₹500)
        if received < 500 or sent < 500:
            continue
        last = max(t.posted_at for t in members)
        # Best display name — prefer normalized merchant
        name = next((t.merchant_normalized for t in members if t.merchant_normalized), cp)
        loops.append(FriendLoop(
            counterparty=name,
            sent=round(sent, 2),
            received=round(received, 2),
            net=round(sent - received, 2),
            sent_count=len(debits),
            received_count=len(credits),
            last_activity=last,
        ))
    loops.sort(key=lambda f: -(f.sent + f.received))
    return loops


def detect_anomalies(db: Session, min_months: int = 3) -> list[CategoryAnomaly]:
    """Flag months where category spend is more than 2σ above its multi-month baseline."""
    txns = db.query(Transaction).filter(
        Transaction.is_duplicate.is_(False),
        Transaction.direction == TxnDirection.DEBIT,
    ).all()
    by_cat_month: dict[tuple[str, str], float] = defaultdict(float)
    for t in txns:
        cat = t.category or "uncategorized"
        by_cat_month[(cat, _month_key(t.posted_at))] += t.amount

    by_cat: dict[str, list[float]] = defaultdict(list)
    by_cat_breakdown: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (cat, month), amt in by_cat_month.items():
        by_cat[cat].append(amt)
        by_cat_breakdown[cat].append((month, amt))

    anomalies: list[CategoryAnomaly] = []
    for cat, amounts in by_cat.items():
        if len(amounts) < min_months:
            continue
        baseline = mean(amounts)
        sd = stdev(amounts) if len(amounts) > 1 else 0
        if sd == 0:
            continue
        for month, amt in by_cat_breakdown[cat]:
            z = (amt - baseline) / sd
            if z > 2:
                anomalies.append(CategoryAnomaly(
                    category=cat,
                    month=month,
                    amount=round(amt, 2),
                    baseline=round(baseline, 2),
                    z_score=round(z, 2),
                ))
    anomalies.sort(key=lambda a: -a.z_score)
    return anomalies


def detect_top_counterparties(db: Session, limit: int = 15) -> list[TopCounterparty]:
    """Top recipients of money flowing OUT. Useful for spotting unlabeled vendors."""
    txns = db.query(Transaction).filter(
        Transaction.is_duplicate.is_(False),
        Transaction.direction == TxnDirection.DEBIT,
    ).all()
    sums: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    for t in txns:
        cp = _norm_counterparty(t)
        if not cp:
            continue
        display = t.merchant_normalized or cp
        sums[display] += t.amount
        counts[display] += 1
    rows = sorted(sums.items(), key=lambda kv: -kv[1])[:limit]
    return [
        TopCounterparty(counterparty=k, total=round(v, 2),
                       count=counts[k], direction="debit")
        for k, v in rows
    ]
