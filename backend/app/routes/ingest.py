"""Statement upload."""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest import UnsupportedStatementError, load_file, supported_formats
from app.llm.client import get_llm
from app.pipeline.nodes import PipelineState, node_llm_tag, node_rule_tag
from app.schemas import IngestFileResult, SupportedFormat
from app.seed import seed_all

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])

ALLOWED_SUFFIXES = {".pdf", ".csv"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Everything outside this set is stripped from an uploaded filename.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(filename: str | None) -> str:
    """Reduce a client-supplied filename to a harmless basename.

    `UploadFile.filename` comes from the client and cannot be trusted. Joining it
    onto a directory unchecked lets `../../` climb out of the intended folder, so
    only the basename survives and anything unusual is replaced.
    """
    base = Path(filename or "upload").name
    cleaned = _UNSAFE.sub("_", base).lstrip(".")
    return cleaned or "upload"


@router.get("/formats", response_model=list[SupportedFormat])
def formats() -> list[SupportedFormat]:
    """Statement formats this build can read. Drives the upload page's hint text."""
    return [SupportedFormat(**fmt) for fmt in supported_formats()]


@router.post("/file", response_model=IngestFileResult)
async def ingest_file(
    file: UploadFile,
    categorise: bool = True,
    db: Session = Depends(get_db),
) -> IngestFileResult:
    """Parse an uploaded statement, store the new rows, then categorise them."""
    name = _safe_name(file.filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            415,
            f"Unsupported file type '{suffix}'. Expected one of "
            f"{', '.join(sorted(ALLOWED_SUFFIXES))}.",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(400, "That file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        )

    seed_all(db)

    # Parse from a temporary file that is removed immediately afterwards.
    # Statements are the most sensitive thing this app handles and there is no
    # reason to leave a second copy on disk once the rows are extracted.
    with tempfile.TemporaryDirectory(prefix="wimmg-upload-") as tmpdir:
        target = Path(tmpdir) / name
        target.write_bytes(payload)
        try:
            result = load_file(db, target)
        except UnsupportedStatementError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            logger.exception("Failed to parse %s", name)
            raise HTTPException(422, f"Could not parse {name}: {e}") from e

    out = IngestFileResult(
        file=result.file, parser=result.parser, parsed=result.parsed,
        inserted=result.inserted, duplicates=result.duplicates,
    )
    if not (categorise and result.inserted_ids):
        return out

    state: PipelineState = {"inserted_ids": result.inserted_ids, "timings_ms": {}}
    state.update(node_rule_tag(state, db))
    state.update(await node_llm_tag(state, db, get_llm()))

    out.rule_tagged = state.get("rule_tagged", 0)
    out.llm_tagged = state.get("llm_tagged", 0)
    out.needs_review = state.get("needs_review", 0)
    out.llm_available = state.get("llm_available", True)
    return out
