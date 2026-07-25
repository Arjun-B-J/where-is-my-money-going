"""Listing transactions and correcting their categories."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TagSource, Transaction, TxnDirection
from app.schemas import BulkTagRequest, TransactionOut, TransactionTagUpdate

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    category: str | None = None,
    untagged: bool | None = Query(
        None, description="True returns only rows nothing has categorised yet."
    ),
    needs_review: bool | None = None,
    direction: str | None = Query(None, pattern="^(debit|credit)$"),
    person_id: int | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    query = db.query(Transaction)

    if category:
        query = query.filter(Transaction.category == category)
    if untagged is not None:
        # Distinct from category="uncategorized": that is a decision the model
        # made, this is the absence of any decision at all.
        query = query.filter(
            Transaction.category.is_(None) if untagged else Transaction.category.isnot(None)
        )
    if needs_review is not None:
        query = query.filter(Transaction.needs_review.is_(needs_review))
    if direction:
        query = query.filter(Transaction.direction == TxnDirection(direction))
    if person_id is not None:
        query = query.filter(Transaction.person_id == person_id)
    if search:
        query = query.filter(Transaction.raw_description.ilike(f"%{search}%"))

    rows = (
        query.order_by(Transaction.posted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [TransactionOut.model_validate(row) for row in rows]


@router.get("/{txn_id}", response_model=TransactionOut)
def get_transaction(txn_id: int, db: Session = Depends(get_db)) -> TransactionOut:
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "No such transaction.")
    return TransactionOut.model_validate(txn)


@router.patch("/{txn_id}/tag", response_model=TransactionOut)
def set_tag(
    txn_id: int,
    update: TransactionTagUpdate,
    db: Session = Depends(get_db),
) -> TransactionOut:
    """Override a category by hand.

    A human decision is final: confidence goes to 1.0, provenance to `user`, and
    the row leaves the review queue. Later pipeline runs skip rows that already
    have a category, so this is never silently undone.
    """
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "No such transaction.")

    txn.category = update.category
    txn.subcategory = update.subcategory
    if update.person_id is not None:
        txn.person_id = update.person_id
    txn.tag_source = TagSource.USER
    txn.tag_confidence = 1.0
    txn.tag_reason = "set by you"
    txn.needs_review = False
    db.commit()
    db.refresh(txn)
    return TransactionOut.model_validate(txn)


@router.post("/bulk-tag")
def bulk_tag(request: BulkTagRequest, db: Session = Depends(get_db)) -> dict:
    """Apply one category to many transactions — the review queue's main action."""
    rows = (
        db.query(Transaction)
        .filter(Transaction.id.in_(request.transaction_ids))
        .all()
    )
    for txn in rows:
        txn.category = request.category
        txn.subcategory = request.subcategory
        txn.tag_source = TagSource.USER
        txn.tag_confidence = 1.0
        txn.tag_reason = "set by you, in bulk"
        txn.needs_review = False
    db.commit()

    missing = len(request.transaction_ids) - len(rows)
    return {
        "updated": len(rows),
        "not_found": missing,
        "category": request.category,
    }
