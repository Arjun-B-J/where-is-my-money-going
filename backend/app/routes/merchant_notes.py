"""Your own annotations for payees.

These populate the "Note" column of the report. They live in your database
rather than in the report generator's source, because an annotation about a
specific payee is a fact about your life and not about the software.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MerchantNote
from app.schemas import MerchantNoteIn, MerchantNoteOut

router = APIRouter(prefix="/merchant-notes", tags=["merchant-notes"])


@router.get("", response_model=list[MerchantNoteOut])
def list_notes(db: Session = Depends(get_db)) -> list[MerchantNoteOut]:
    rows = (
        db.query(MerchantNote)
        .order_by(MerchantNote.priority.asc(), MerchantNote.pattern.asc())
        .all()
    )
    return [MerchantNoteOut.model_validate(row) for row in rows]


@router.put("", response_model=MerchantNoteOut)
def upsert_note(payload: MerchantNoteIn, db: Session = Depends(get_db)) -> MerchantNoteOut:
    """Create or update the note for a payee pattern.

    Idempotent on `pattern` so the UI can save without tracking whether a note
    already existed.
    """
    pattern = payload.pattern.strip().upper()
    note = db.query(MerchantNote).filter(MerchantNote.pattern == pattern).first()
    if note is None:
        note = MerchantNote(pattern=pattern)
        db.add(note)
    note.note = payload.note.strip()
    note.priority = payload.priority
    db.commit()
    db.refresh(note)
    return MerchantNoteOut.model_validate(note)


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)) -> dict:
    note = db.get(MerchantNote, note_id)
    if note is None:
        raise HTTPException(404, "No such note.")
    db.delete(note)
    db.commit()
    return {"deleted": note_id}
