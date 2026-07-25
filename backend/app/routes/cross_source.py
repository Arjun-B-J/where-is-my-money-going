"""Checks that compare two statements against each other."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.cross_source import (
    find_possible_duplicates,
    reconcile_credit_card_payments,
)

router = APIRouter(prefix="/cross-source", tags=["cross-source"])


@router.get("/card-reconcile")
def card_reconcile(db: Session = Depends(get_db)) -> list[dict]:
    """Card-bill payments as seen from the bank side and the card side."""
    return [asdict(row) for row in reconcile_credit_card_payments(db)]


@router.get("/duplicates")
def duplicates(
    day_tolerance: int = Query(2, ge=0, le=7), db: Session = Depends(get_db)
) -> list[dict]:
    """The same purchase appearing on two different accounts."""
    return [asdict(hit) for hit in find_possible_duplicates(db, day_tolerance=day_tolerance)]
