"""Health and reset."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import APP_NAME, APP_VERSION, get_settings
from app.db import get_db
from app.llm.client import get_llm
from app.models import PipelineRun, Transaction

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health(db: Session = Depends(get_db)) -> dict:
    """Report app and model status.

    Always returns 200 with a status body rather than failing when the model is
    down — the UI needs to be able to say *what* is wrong.
    """
    settings = get_settings()
    llm_status = await get_llm().health()
    return {
        "ok": True,
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "llm": llm_status,
        "config": {
            "llm_first": settings.llm_first,
            "confidence_threshold": settings.confidence_threshold,
            "thinking_enabled": settings.llm_think,
        },
        "stats": {
            "transactions": db.query(Transaction).count(),
            "pipeline_runs": db.query(PipelineRun).count(),
            "needs_review": db.query(Transaction)
                              .filter(Transaction.needs_review.is_(True)).count(),
        },
    }


@router.post("/reset")
def reset(db: Session = Depends(get_db)) -> dict:
    """Delete all transactions and run history.

    Categories, rules, payee notes and people survive — they are configuration,
    not data. Dropping everything is deliberately not an API call; use
    `wimmg reset --all` or delete the SQLite file.
    """
    transactions = db.query(Transaction).delete()
    runs = db.query(PipelineRun).delete()
    db.commit()
    return {"deleted": {"transactions": transactions, "pipeline_runs": runs}}
