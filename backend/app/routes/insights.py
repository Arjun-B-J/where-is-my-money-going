"""Dashboard insight cards."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import InsightCard
from app.services.insights import generate_insights

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=list[InsightCard])
async def insights(
    months: int = Query(12, ge=1, le=60), db: Session = Depends(get_db)
) -> list[InsightCard]:
    return await generate_insights(db, months=months)
