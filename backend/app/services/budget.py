"""Budget envelopes — per-category monthly caps with traffic-light status.

Heuristic baseline: the median of the last 6 months' spend per category.
The user can override (UI v0.5) or accept the auto-baseline.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.clock import utc_now
from app.config import get_settings
from app.models import Transaction, TxnDirection


@dataclass
class BudgetRow:
    category: str
    month: str               # YYYY-MM (current focus month)
    spent: float
    suggested_budget: float
    pct_used: float
    status: str              # "good" | "warn" | "critical"
    history_baseline: float  # median of past 6 months


def auto_budgets(db: Session, focus_month: str | None = None) -> list[BudgetRow]:
    """Return the current-month budget status for every category."""
    s = get_settings()

    if focus_month is None:
        focus_month = utc_now().strftime("%Y-%m")

    txns = db.query(Transaction).filter(
        Transaction.is_duplicate.is_(False),
        Transaction.direction == TxnDirection.DEBIT,
    ).all()

    by_cat_month: dict[tuple[str, str], float] = defaultdict(float)
    for t in txns:
        cat = t.category or "uncategorized"
        m = t.posted_at.strftime("%Y-%m")
        by_cat_month[(cat, m)] += t.amount

    # Per-category history (excluding the focus month)
    cat_history: dict[str, list[float]] = defaultdict(list)
    for (cat, m), amt in by_cat_month.items():
        if m == focus_month:
            continue
        cat_history[cat].append(amt)

    out: list[BudgetRow] = []
    cats_with_focus = {c for (c, m) in by_cat_month if m == focus_month}
    cats_with_history = set(cat_history.keys())
    all_cats = cats_with_focus | cats_with_history

    for cat in sorted(all_cats):
        history = cat_history.get(cat, [])
        baseline = statistics.median(history) if history else 0
        # Suggested budget: 110% of median (a little headroom)
        budget = round(baseline * 1.1, 2) if baseline > 0 else 0
        spent = round(by_cat_month.get((cat, focus_month), 0), 2)
        pct = (spent / budget) if budget > 0 else 0
        if pct >= s.budget_critical_pct:
            status = "critical"
        elif pct >= s.budget_warning_pct:
            status = "warn"
        else:
            status = "good"
        out.append(BudgetRow(
            category=cat,
            month=focus_month,
            spent=spent,
            suggested_budget=budget,
            pct_used=round(pct, 2),
            status=status,
            history_baseline=round(baseline, 2),
        ))
    out.sort(key=lambda r: -r.suggested_budget)
    return out


@dataclass
class WeekdayPattern:
    weekday: str             # Monday..Sunday
    debit_total: float
    credit_total: float
    debit_count: int


def weekday_pattern(db: Session, months: int = 6) -> list[WeekdayPattern]:
    """How spending varies across days of the week."""
    cutoff = utc_now() - timedelta(days=months * 31)
    txns = db.query(Transaction).filter(
        Transaction.posted_at >= cutoff,
        Transaction.is_duplicate.is_(False),
    ).all()

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_dow: dict[str, dict[str, float]] = {d: {"d": 0, "c": 0, "n": 0} for d in days}
    for t in txns:
        d = days[t.posted_at.weekday()]
        if t.direction == TxnDirection.DEBIT:
            by_dow[d]["d"] += t.amount
            by_dow[d]["n"] += 1
        else:
            by_dow[d]["c"] += t.amount

    return [
        WeekdayPattern(
            weekday=d,
            debit_total=round(by_dow[d]["d"], 2),
            credit_total=round(by_dow[d]["c"], 2),
            debit_count=int(by_dow[d]["n"]),
        )
        for d in days
    ]
