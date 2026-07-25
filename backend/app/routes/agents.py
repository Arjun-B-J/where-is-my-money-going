"""Running the agents on demand, outside a full pipeline run."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.llm.client import get_llm
from app.pipeline.validator import classify_relationships, revalidate_low_confidence
from app.services.friend_detector import detect_friends, link_detected_friends

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/friend-detect")
def preview_friends(db: Session = Depends(get_db)) -> list[dict]:
    """Show what the detector would find, without writing anything."""
    return [asdict(friend) for friend in detect_friends(db)]


@router.post("/friend-detect")
def apply_friends(
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> dict:
    """Create Person rows for detected people and link their transactions."""
    threshold = (
        min_confidence
        if min_confidence is not None
        else get_settings().friend_min_confidence
    )
    detected = detect_friends(db)
    stats = link_detected_friends(db, detected, min_confidence=threshold)
    return {
        "candidates": len(detected),
        "threshold": threshold,
        **stats,
        "applied": [
            asdict(friend) for friend in detected if friend.confidence >= threshold
        ],
    }


@router.post("/validate")
async def validate(
    limit: int | None = Query(None, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Re-check low-confidence tags and classify relationships."""
    settings = get_settings()
    llm = get_llm()
    decisions = await revalidate_low_confidence(
        db, llm=llm,
        threshold=settings.confidence_threshold,
        limit=limit or settings.validator_max_per_run,
    )
    relationships = await classify_relationships(db, llm=llm)
    return {
        "reviewed": len(decisions),
        "confirmed": sum(1 for d in decisions if d.agreed),
        "overridden": sum(1 for d in decisions if not d.agreed),
        "relationships": relationships,
    }
