"""People graph — list, get details, balances."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Person, Transaction, TxnDirection
from app.schemas import PersonBalance, PersonOut

router = APIRouter(prefix="/people", tags=["people"])


def _iou_balance(txns: list[Transaction], is_friend: bool) -> float:
    """For friends: every transaction is part of the IOU ledger.
    For vendors / services: only loan-tagged transactions count."""
    ledger = txns if is_friend else [t for t in txns if t.is_loan]
    return (
        sum(t.amount for t in ledger if t.direction == TxnDirection.DEBIT)
        - sum(t.amount for t in ledger if t.direction == TxnDirection.CREDIT)
    )


@router.get("", response_model=list[PersonBalance])
def list_people(db: Session = Depends(get_db)) -> list[PersonBalance]:
    out: list[PersonBalance] = []
    for p in db.query(Person).all():
        txns = db.query(Transaction).filter(Transaction.person_id == p.id).all()
        out.append(PersonBalance(
            person=PersonOut.model_validate(p),
            they_owe_you=round(_iou_balance(txns, p.relationship_type == "friend"), 2),
            transaction_count=len(txns),
        ))
    out.sort(key=lambda b: abs(b.they_owe_you), reverse=True)
    return out


@router.get("/{person_id}", response_model=PersonBalance)
def get_person(person_id: int, db: Session = Depends(get_db)) -> PersonBalance:
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Person not found")
    txns = db.query(Transaction).filter(Transaction.person_id == p.id).all()
    return PersonBalance(
        person=PersonOut.model_validate(p),
        they_owe_you=round(_iou_balance(txns, p.relationship_type == "friend"), 2),
        transaction_count=len(txns),
    )
