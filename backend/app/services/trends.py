"""Time-series breakdowns: daily, weekly, monthly trends."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.clock import utc_now
from app.models import Transaction, TxnDirection


@dataclass
class Bucket:
    period: str
    debit: float
    credit: float
    net: float
    transaction_count: int


def _iso_week(d: datetime) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# How a transaction's timestamp maps to a bucket label, per granularity.
_BUCKET_KEYS: dict[str, Callable[[Transaction], str]] = {
    "daily": lambda t: t.posted_at.strftime("%Y-%m-%d"),
    "weekly": lambda t: _iso_week(t.posted_at),
    "monthly": lambda t: t.posted_at.strftime("%Y-%m"),
}


def trends(db: Session, granularity: str = "monthly", months: int = 12) -> list[Bucket]:
    """Return cash-flow buckets at the requested granularity."""
    cutoff = utc_now() - timedelta(days=months * 31)
    txns = (
        db.query(Transaction)
        .filter(Transaction.posted_at >= cutoff, Transaction.is_duplicate.is_(False))
        .all()
    )

    bucket_key = _BUCKET_KEYS.get(granularity)
    if bucket_key is None:
        raise ValueError(
            f"unknown granularity {granularity!r}; expected one of "
            f"{', '.join(sorted(_BUCKET_KEYS))}"
        )

    sums: dict[str, dict[str, float]] = defaultdict(lambda: {"d": 0.0, "c": 0.0, "n": 0})
    for t in txns:
        b = sums[bucket_key(t)]
        if t.direction == TxnDirection.DEBIT:
            b["d"] += t.amount
        else:
            b["c"] += t.amount
        b["n"] += 1

    out = [
        Bucket(
            period=k,
            debit=round(v["d"], 2),
            credit=round(v["c"], 2),
            net=round(v["c"] - v["d"], 2),
            transaction_count=int(v["n"]),
        )
        for k, v in sums.items()
    ]
    out.sort(key=lambda x: x.period)
    return out


@dataclass
class Forecast:
    """Linear-extrapolation forecast for the next N months."""
    next_periods: list[str]
    debit_forecast: list[float]
    credit_forecast: list[float]
    net_forecast: list[float]
    method: str


def linear_forecast(buckets: list[Bucket], horizon_months: int = 3) -> Forecast:
    """Simple linear regression on the last 6 monthly buckets.

    For sparse history (< 3 full months), falls back to averaging just the
    months that *do* have data. The current-month bucket is excluded from
    the fit because it's incomplete and would drag projections to zero.
    """
    # Ignore the current (in-progress) month — it always understates
    today_period = utc_now().strftime("%Y-%m")
    historical = [b for b in buckets if b.period < today_period]

    if len(historical) < 2:
        return Forecast(
            next_periods=[], debit_forecast=[], credit_forecast=[],
            net_forecast=[], method="insufficient-history",
        )

    history = historical[-6:]
    n = len(history)
    debit_avg = sum(b.debit for b in history) / n
    credit_avg = sum(b.credit for b in history) / n
    xs = list(range(n))
    debit_ys = [b.debit for b in history]
    credit_ys = [b.credit for b in history]

    def _slope(xs: list[int], ys: list[float]) -> float:
        if len(xs) < 2:
            return 0.0
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
        den = sum((x - mx) ** 2 for x in xs) or 1
        return num / den

    d_slope = _slope(xs, debit_ys)
    c_slope = _slope(xs, credit_ys)

    last_period = history[-1].period
    last_year, last_month = map(int, last_period.split("-"))
    next_periods: list[str] = []
    debit_proj: list[float] = []
    credit_proj: list[float] = []
    for i in range(1, horizon_months + 1):
        m = last_month + i
        y = last_year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        next_periods.append(f"{y}-{m:02d}")
        # Project as <average + slope * future_index>; clamp at 0 for sanity
        future_index = n - 1 + i
        debit_proj.append(round(max(0.0, debit_avg + d_slope * future_index), 2))
        credit_proj.append(round(max(0.0, credit_avg + c_slope * future_index), 2))

    return Forecast(
        next_periods=next_periods,
        debit_forecast=debit_proj,
        credit_forecast=credit_proj,
        net_forecast=[round(c - d, 2) for c, d in zip(credit_proj, debit_proj, strict=True)],
        method=f"linear-regression-on-last-{n}",
    )
