"""Parser for HDFC credit-card CSV exports.

Layout:

    <account/holder header lines, then a blank line>
    "Transaction Details:"
    "Date","Sr.No.","Transaction Details","Reward Points","Intl.Amount","Amount(in Rs)","Sign"
    "<card last4> <holder name>"          <- banner row, no date
    "03-APR-26","1","POS SOME SHOP","12","","1,299.00","1,299.00"

The header row is located by content rather than by line number, because the
number of preamble lines varies between exports. A negative rupee amount means
a refund or a payment towards the card.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path

from app.ingest.normalize import normalize_merchant
from app.ingest.records import ParsedTxn
from app.models import TxnDirection, TxnSource

logger = logging.getLogger(__name__)

_DATE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{2}$")
_DATE_COL, _DESC_COL, _AMOUNT_COL = 0, 2, 5
_MIN_COLUMNS = 6


def _find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows):
        if row and row[0].strip().lower() == "date" and len(row) >= _MIN_COLUMNS:
            return i
    return None


def parse(path: str | Path, source: TxnSource = TxnSource.CARD) -> list[ParsedTxn]:
    """Extract transactions from one credit-card CSV export."""
    with open(Path(path), encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    header = _find_header(rows)
    if header is None:
        logger.warning("hdfc_credit_csv: no 'Date' header row found — nothing parsed")
        return []

    txns: list[ParsedTxn] = []
    for row in rows[header + 1:]:
        if len(row) < _MIN_COLUMNS or not _DATE.match(row[_DATE_COL].strip()):
            # Banner rows, blank rows and trailing totals all land here.
            continue
        try:
            posted = datetime.strptime(row[_DATE_COL].strip(), "%d-%b-%y").replace(hour=12)
            amount = float(row[_AMOUNT_COL].replace(",", "").strip() or 0)
        except ValueError:
            continue

        description = row[_DESC_COL].strip()
        if not description or amount == 0:
            continue

        txns.append(ParsedTxn(
            posted_at=posted,
            amount=abs(amount),
            direction=TxnDirection.CREDIT if amount < 0 else TxnDirection.DEBIT,
            source=source,
            raw_description=description,
            merchant_normalized=normalize_merchant(description),
            extra_metadata={"parser": "hdfc_credit_csv"},
        ))

    logger.info("hdfc_credit_csv: extracted %d transactions", len(txns))
    return txns
