"""Turning `ParsedTxn` records into database rows.

The one interesting property here is idempotency: dropping the same statement
in twice, or two statements whose date ranges overlap, must not double-count
anything. That is enforced by `external_id` (see `app.ingest.records`) plus the
`uq_txn_external` constraint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingest.parsers import UnsupportedStatementError, parse_file
from app.ingest.records import ParsedTxn
from app.models import Transaction, TxnSource

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    """What one load did. `duplicates` is the count skipped, not an error."""

    inserted_ids: list[int]
    duplicates: int
    parsed: int

    @property
    def inserted(self) -> int:
        return len(self.inserted_ids)


@dataclass
class FileLoadResult(LoadResult):
    file: str = ""
    parser: str = ""


def load_transactions(db: Session, records: list[ParsedTxn]) -> LoadResult:
    """Insert records, skipping any whose (external_id, source) already exists."""
    if not records:
        return LoadResult(inserted_ids=[], duplicates=0, parsed=0)

    # One query for all incoming ids beats one query per record. On a 12-month
    # multi-statement load that is ~2000 queries saved.
    incoming = {(r.external_id, r.source) for r in records}
    existing = {
        (external_id, source)
        for external_id, source in db.query(
            Transaction.external_id, Transaction.source
        ).filter(
            Transaction.external_id.in_({eid for eid, _ in incoming})
        ).all()
    }

    inserted_ids: list[int] = []
    duplicates = 0
    seen_in_batch: set[tuple[str, TxnSource]] = set()

    for record in records:
        key = (record.external_id, record.source)
        # Guard against duplicates inside a single batch too — overlapping
        # statements are a normal case, not an exceptional one.
        if key in existing or key in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(key)

        txn = Transaction(
            external_id=record.external_id,
            posted_at=record.posted_at,
            amount=record.amount,
            direction=record.direction,
            source=record.source,
            raw_description=record.raw_description,
            merchant_normalized=record.merchant_normalized,
            counterparty_id=record.counterparty_id,
            balance_after=record.balance_after,
            extra_metadata=record.extra_metadata or {},
        )
        db.add(txn)
        db.flush()
        inserted_ids.append(txn.id)

    db.commit()
    logger.info("Loaded %d new transactions (%d duplicates skipped)",
                len(inserted_ids), duplicates)
    return LoadResult(
        inserted_ids=inserted_ids, duplicates=duplicates, parsed=len(records)
    )


def load_file(
    db: Session, path: str | Path, source: TxnSource | None = None
) -> FileLoadResult:
    """Parse and load a single statement file."""
    path = Path(path)
    from app.ingest.parsers import detect

    parser = detect(path)
    records = parse_file(path, source)
    result = load_transactions(db, records)
    return FileLoadResult(
        inserted_ids=result.inserted_ids,
        duplicates=result.duplicates,
        parsed=result.parsed,
        file=path.name,
        parser=parser.name if parser else "unknown",
    )


def load_directory(
    db: Session, directory: str | Path, source: TxnSource | None = None
) -> list[FileLoadResult]:
    """Load every statement in a directory, skipping files nothing can read."""
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"{directory} is not a directory")

    results: list[FileLoadResult] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".csv"}:
            continue
        try:
            results.append(load_file(db, path, source))
        except UnsupportedStatementError as e:
            logger.info("Skipping %s: %s", path.name, e)
        except Exception:
            logger.exception("Failed to load %s", path.name)
    return results
