"""Dashboard summary endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import DashboardSummary
from app.services.analytics import dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(months: int = 12, db: Session = Depends(get_db)) -> DashboardSummary:
    return dashboard_summary(db, months=months)
