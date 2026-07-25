"""Receipt scanning with the local vision model."""
from __future__ import annotations

import base64
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.clock import utc_now
from app.db import get_db
from app.ingest.records import ParsedTxn
from app.llm.client import get_llm
from app.llm.prompts import RECEIPT_SCHEMA, RECEIPT_SYSTEM
from app.models import TagSource, Transaction, TxnDirection, TxnSource
from app.schemas import TransactionOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/receipt", tags=["receipt"])

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@router.post("/scan")
async def scan_receipt(
    file: UploadFile = File(...),
    save: bool = True,
    db: Session = Depends(get_db),
) -> dict:
    """Read a receipt photo and optionally store it as a transaction.

    Unlike statement parsing, this genuinely depends on the model — there is no
    deterministic path for pixels. So the response says plainly whether the model
    answered, and nothing is written when it did not.
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            415, f"Expected an image ({', '.join(sorted(ALLOWED_TYPES))})."
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(400, "That image is empty.")
    if len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB.")

    result = await get_llm().vision(
        prompt=RECEIPT_SYSTEM,
        image_b64=base64.b64encode(payload).decode("ascii"),
        schema=RECEIPT_SCHEMA,
    )
    extracted = result.json()
    if extracted is None:
        return {
            "ok": False,
            "error": result.error or "The vision model returned nothing usable.",
            "extracted": None,
            "transaction": None,
        }

    if not extracted.get("is_receipt"):
        return {
            "ok": True,
            "error": None,
            "extracted": extracted,
            "transaction": None,
            "message": "That image does not look like a receipt.",
        }

    merchant = (extracted.get("merchant") or "").strip()
    amount = extracted.get("amount")
    if not merchant or not amount or float(amount) <= 0:
        return {
            "ok": True,
            "error": None,
            "extracted": extracted,
            "transaction": None,
            "message": "Could not read a merchant and total from that receipt.",
        }

    if not save:
        return {"ok": True, "error": None, "extracted": extracted, "transaction": None}

    record = ParsedTxn(
        posted_at=_parse_date(extracted.get("date")),
        amount=float(amount),
        direction=TxnDirection.DEBIT,
        source=TxnSource.RECEIPT,
        raw_description=f"Receipt: {merchant}",
        merchant_normalized=merchant,
        extra_metadata={"items": extracted.get("items", []), "scanned": True},
    )

    existing = (
        db.query(Transaction)
        .filter(
            Transaction.external_id == record.external_id,
            Transaction.source == record.source,
        )
        .first()
    )
    if existing is not None:
        return {
            "ok": True, "error": None, "extracted": extracted,
            "transaction": TransactionOut.model_validate(existing).model_dump(),
            "message": "This receipt was already saved.",
        }

    txn = Transaction(
        external_id=record.external_id,
        posted_at=record.posted_at,
        amount=record.amount,
        direction=record.direction,
        source=record.source,
        raw_description=record.raw_description,
        merchant_normalized=record.merchant_normalized,
        category=extracted.get("category", "uncategorized"),
        tag_source=TagSource.LLM,
        tag_confidence=float(extracted.get("confidence") or 0.0),
        tag_reason="read from a receipt image by the vision model",
        extra_metadata=record.extra_metadata,
    )
    # A scan the model was unsure about goes to the review queue like any other
    # low-confidence tag.
    txn.needs_review = (txn.tag_confidence or 0.0) < 0.70

    db.add(txn)
    db.commit()
    db.refresh(txn)
    return {
        "ok": True,
        "error": None,
        "extracted": extracted,
        "transaction": TransactionOut.model_validate(txn).model_dump(),
    }


def _parse_date(value: str | None) -> datetime:
    """Parse the model's date, falling back to now when it is missing or invalid."""
    if not value:
        return utc_now()
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").replace(hour=12)
    except ValueError:
        logger.info("Unparseable receipt date %r — using today", value)
        return utc_now()
