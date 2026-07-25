"""Analytics service — aggregations the dashboard consumes."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clock import utc_now
from app.models import (
    Category,
    Person,
    Transaction,
    TxnDirection,
)
from app.schemas import (
    CategorySpend,
    DashboardSummary,
    MerchantSpend,
    MonthlySpend,
    PersonBalance,
    PersonOut,
)
from app.services.cross_source import is_internal_transfer


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def dashboard_summary(db: Session, months: int = 12) -> DashboardSummary:
    cutoff = utc_now() - timedelta(days=months * 31)

    txns = (
        db.query(Transaction)
        .filter(Transaction.posted_at >= cutoff, Transaction.is_duplicate.is_(False))
        .all()
    )

    total_debit = sum(t.amount for t in txns if t.direction == TxnDirection.DEBIT)
    total_credit = sum(t.amount for t in txns if t.direction == TxnDirection.CREDIT)

    # Paying a card bill from a bank account shows up twice in this dataset: once
    # as the bank debit, once as the card purchases it settles. Netting the raw
    # totals therefore reports a deficit that does not exist — and the insight
    # cards, which read these figures, will confidently explain it to the user.
    internal_transfers = sum(t.amount for t in txns if is_internal_transfer(t))
    spend = total_debit - internal_transfers

    # Monthly
    monthly_map: dict[str, dict[str, float]] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0})
    for t in txns:
        m = _month_key(t.posted_at)
        if t.direction == TxnDirection.DEBIT:
            monthly_map[m]["debit"] += t.amount
        else:
            monthly_map[m]["credit"] += t.amount
    monthly = sorted(
        [
            MonthlySpend(month=m, debit_total=round(v["debit"], 2),
                        credit_total=round(v["credit"], 2),
                        net=round(v["credit"] - v["debit"], 2))
            for m, v in monthly_map.items()
        ],
        key=lambda x: x.month,
    )

    # Categories — debits only, and excluding internal transfers so the
    # breakdown adds up to `spend`. A card-bill payment is categorised
    # `loan_repayment`, so leaving it in made that category the largest slice on
    # the chart while the headline figure excluded it — two numbers on one screen
    # that could not both be right.
    cat_essentials = {c.name: c.is_essential for c in db.query(Category).all()}
    cat_map: dict[str, dict[str, float | int]] = defaultdict(lambda: {"total": 0.0, "count": 0})
    for t in txns:
        if t.direction != TxnDirection.DEBIT or is_internal_transfer(t):
            continue
        key = t.category or "uncategorized"
        cat_map[key]["total"] += t.amount
        cat_map[key]["count"] += 1
    by_category = sorted(
        [
            CategorySpend(category=k, total=round(v["total"], 2), count=int(v["count"]),
                         is_essential=cat_essentials.get(k, False))
            for k, v in cat_map.items()
        ],
        key=lambda x: x.total, reverse=True,
    )

    # Top merchants — debits only
    # A small dataclass rather than a dict of mixed value types: the accumulator
    # holds a float, an int and a datetime, and juggling those in one dict means
    # casting on every read.
    @dataclass
    class _MerchantTotals:
        total: float = 0.0
        count: int = 0
        last_seen: datetime = datetime.min

    merch_map: dict[str, _MerchantTotals] = defaultdict(_MerchantTotals)
    for t in txns:
        # "CREDIT CARD PAYMENT" is not a merchant, and it would otherwise top the
        # list of places your money went.
        if t.direction != TxnDirection.DEBIT or is_internal_transfer(t):
            continue
        name = (t.merchant_normalized or t.raw_description or "Unknown").strip()
        entry = merch_map[name]
        entry.total += t.amount
        entry.count += 1
        entry.last_seen = max(entry.last_seen, t.posted_at)

    top_merchants = sorted(
        (
            MerchantSpend(
                merchant=name, total=round(entry.total, 2),
                count=entry.count, last_seen=entry.last_seen,
            )
            for name, entry in merch_map.items()
        ),
        key=lambda row: row.total, reverse=True,
    )[:10]

    # People — running balance.
    #
    # For friends, the entire two-way history is the ledger: money moving both
    # ways between friends *is* an informal IOU. A debit means you sent (so they
    # are behind); a credit means they sent.
    #
    # For vendors, only rows explicitly flagged as loans count. Paying a landlord
    # every month is a service you received, not money you expect back.
    people = db.query(Person).all()
    people_balances: list[PersonBalance] = []
    for p in people:
        pt = (
            db.query(Transaction)
            .filter(Transaction.person_id == p.id, Transaction.is_duplicate.is_(False))
            .all()
        )
        if not pt:
            continue

        # Friends: the whole two-way history is the running balance. Vendors:
        # only rows explicitly marked as loans, since paying rent is not an IOU.
        ledger = pt if p.relationship_type == "friend" else [t for t in pt if t.is_loan]

        owed_to_you = (
            sum(t.amount for t in ledger if t.direction == TxnDirection.DEBIT)
            - sum(t.amount for t in ledger if t.direction == TxnDirection.CREDIT)
        )
        people_balances.append(PersonBalance(
            person=PersonOut.model_validate(p),
            they_owe_you=round(owed_to_you, 2),
            transaction_count=len(pt),
        ))
    people_balances.sort(key=lambda b: abs(b.they_owe_you), reverse=True)

    return DashboardSummary(
        total_debit=round(total_debit, 2),
        spend=round(spend, 2),
        internal_transfers=round(internal_transfers, 2),
        total_credit=round(total_credit, 2),
        net=round(total_credit - spend, 2),
        transaction_count=len(txns),
        needs_review=db.query(func.count(Transaction.id)).filter(
            Transaction.needs_review.is_(True)
        ).scalar() or 0,
        monthly=monthly,
        by_category=by_category,
        top_merchants=top_merchants,
        people=people_balances,
    )
