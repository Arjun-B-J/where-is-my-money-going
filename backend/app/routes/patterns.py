"""Patterns endpoint — recurring payments, friend loops, anomalies, top recipients."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.patterns import (
    detect_anomalies,
    detect_friend_loops,
    detect_recurring,
    detect_top_counterparties,
)

router = APIRouter(prefix="/patterns", tags=["patterns"])


@router.get("/recurring")
def recurring(db: Session = Depends(get_db)) -> list[dict]:
    return [asdict(r) for r in detect_recurring(db)]


@router.get("/friend-loops")
def friend_loops(db: Session = Depends(get_db)) -> list[dict]:
    return [asdict(f) for f in detect_friend_loops(db)]


@router.get("/anomalies")
def anomalies(db: Session = Depends(get_db)) -> list[dict]:
    return [asdict(a) for a in detect_anomalies(db)]


@router.get("/top-counterparties")
def top_counterparties(limit: int = 15, db: Session = Depends(get_db)) -> list[dict]:
    return [asdict(t) for t in detect_top_counterparties(db, limit=limit)]


@router.get("")
def all_patterns(db: Session = Depends(get_db)) -> dict:
    return {
        "recurring": [asdict(r) for r in detect_recurring(db)],
        "friend_loops": [asdict(f) for f in detect_friend_loops(db)],
        "anomalies": [asdict(a) for a in detect_anomalies(db)],
        "top_counterparties": [asdict(t) for t in detect_top_counterparties(db)],
    }
