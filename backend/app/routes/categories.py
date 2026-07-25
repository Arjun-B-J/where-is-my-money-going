"""Categories endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Category

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    cats = db.query(Category).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "icon": c.icon,
            "color": c.color,
            "is_essential": c.is_essential,
        }
        for c in cats
    ]
