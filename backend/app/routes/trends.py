"""Time series and forecast."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.trends import linear_forecast, trends

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("")
def buckets(
    granularity: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    months: int = Query(12, ge=1, le=60),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [asdict(bucket) for bucket in trends(db, granularity=granularity, months=months)]


@router.get("/forecast")
def forecast(
    horizon_months: int = Query(3, ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    """Least-squares projection of monthly spend.

    A straight line through recent months. It has no notion of seasonality or of
    a one-off large purchase, so it is a rough extrapolation rather than a
    prediction — the response includes the history so the caller can judge it.
    """
    history = trends(db, granularity="monthly", months=12)
    return {
        "history": [asdict(bucket) for bucket in history],
        "forecast": asdict(linear_forecast(history, horizon_months=horizon_months)),
        "method": "least-squares linear fit on monthly totals; no seasonality",
    }
