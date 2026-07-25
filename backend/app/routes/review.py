"""The review queue.

Transactions needing review cluster heavily by payee: fifteen transfers to the
same person are one decision, not fifteen. Grouping them is what makes clearing
a real month's backlog take minutes instead of an evening.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.group_by_recipient import group_unreviewed

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/groups")
def groups(
    limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)
) -> list[dict]:
    """Unreviewed transactions clustered by payee, largest cluster first."""
    return [asdict(group) for group in group_unreviewed(db, limit=limit)]
