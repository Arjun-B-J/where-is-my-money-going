"""Budget envelopes and the day-of-week pattern."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.budget import auto_budgets, weekday_pattern

router = APIRouter(prefix="/budget", tags=["budget"])


@router.get("/envelopes")
def envelopes(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Per-category caps suggested from your own history.

    The suggestion is the median of previous months for that category, not a
    recommendation about how you ought to live.
    """
    return [asdict(row) for row in auto_budgets(db, focus_month=month)]


@router.get("/weekday-pattern")
def weekday(
    months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)
) -> list[dict]:
    return [asdict(row) for row in weekday_pattern(db, months=months)]
