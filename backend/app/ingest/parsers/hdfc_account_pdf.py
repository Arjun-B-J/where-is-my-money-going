"""Parser for HDFC savings/current-account PDF statements.

The statement layout is:

    Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance
    02/01/26 UPI-SOME PAYEE-... 0000... 02/01/26 570.00      9,512.03

Two properties of this format drive the implementation:

**Column alignment is lost.** `pdfplumber` extracts text, not a grid, so the
withdrawal and deposit columns collapse into one whitespace-separated run. You
cannot tell a debit from a credit by which column the number sat in.

**Rows wrap.** A long narration continues on the next line with no leading date.

So direction is inferred from the *change in closing balance* rather than from
column position or keyword matching — the balance is the one number the bank
guarantees is correct. See docs/DECISIONS.md §2.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from app.ingest.normalize import split_upi_narration
from app.ingest.records import ParsedTxn
from app.models import TxnDirection, TxnSource

logger = logging.getLogger(__name__)

_DATE_PREFIX = re.compile(r"^(\d{2}/\d{2}/\d{2})\s")
_AMOUNT = re.compile(r"\d[\d,]*\.\d{2}")
_TRAILING_VALUE_DATE = re.compile(r"\s+\d{2}/\d{2}/\d{2}\s*$")
_TRAILING_REF = re.compile(r"\s+0?\d{10,}\s*$")

# HDFC prints a per-page summary row carrying several amounts at once. Any
# continuation line with this many amounts is chrome, not transaction text.
_SUMMARY_ROW_AMOUNT_COUNT = 3

# Credit markers, used only for the very first row of a statement where there is
# no previous balance to diff against.
_CREDIT_HINTS = (
    "neft cr", "imps cr", "rtgs cr", "interest credit", "int.pd",
    "salary", "refund", "reversal", "cashback", "deposit",
)


def _parse_date(text: str) -> datetime | None:
    try:
        # Statements use DD/MM/YY. Noon avoids any date shifting on display.
        return datetime.strptime(text, "%d/%m/%y").replace(hour=12)
    except ValueError:
        return None


def _amount(text: str) -> float:
    return float(text.replace(",", ""))


def _read_lines(pdf_path: Path) -> list[str]:
    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(line.rstrip() for line in text.splitlines() if line.strip())
    return lines


def _group_rows(lines: list[str]) -> list[dict]:
    """Join wrapped continuation lines into one record per transaction."""
    rows: list[dict] = []
    current: dict | None = None

    for line in lines:
        match = _DATE_PREFIX.match(line)
        if match:
            if current:
                rows.append(current)
            date = _parse_date(match.group(1))
            if date is None:
                current = None
                continue
            current = {"date": date, "text": line[match.end():]}
        elif current is not None:
            stripped = line.strip()
            if len(_AMOUNT.findall(stripped)) >= _SUMMARY_ROW_AMOUNT_COUNT:
                # Page summary — close the open record and stop appending.
                rows.append(current)
                current = None
                continue
            current["text"] += " " + stripped

    if current:
        rows.append(current)
    return rows


def _strip_trailing_columns(text: str, amounts: list[str]) -> str:
    """Remove the amount/balance columns and reference noise from a narration."""
    desc = text
    for amt in amounts[-2:]:
        idx = desc.rfind(amt)
        if idx >= 0:
            desc = desc[:idx]
    desc = _TRAILING_VALUE_DATE.sub("", desc.strip())
    desc = _TRAILING_REF.sub("", desc.strip())
    return desc.strip()


def _first_row_direction(description: str) -> TxnDirection:
    low = description.lower()
    return (
        TxnDirection.CREDIT
        if any(hint in low for hint in _CREDIT_HINTS)
        else TxnDirection.DEBIT
    )


def parse(path: str | Path, source: TxnSource = TxnSource.BANK) -> list[ParsedTxn]:
    """Extract transactions from one account-statement PDF."""
    rows = _group_rows(_read_lines(Path(path)))

    txns: list[ParsedTxn] = []
    previous_balance: float | None = None
    skipped = 0

    for row in rows:
        amounts = _AMOUNT.findall(row["text"])
        if len(amounts) < 2:
            # Needs both a transaction amount and a closing balance.
            skipped += 1
            continue

        closing = _amount(amounts[-1])
        amount = _amount(amounts[-2])
        if amount <= 0:
            # Fee/summary artefact. Still advance the balance so the next row
            # diffs against the right figure.
            previous_balance = closing
            skipped += 1
            continue

        if previous_balance is None:
            direction = _first_row_direction(row["text"])
        else:
            delta = closing - previous_balance
            # A balance that did not move means the amount we picked up is not a
            # real movement; treat as a debit but it will usually be filtered
            # above. Tolerance guards against float noise in parsed decimals.
            direction = TxnDirection.CREDIT if delta > 0.01 else TxnDirection.DEBIT
        previous_balance = closing

        description = _strip_trailing_columns(row["text"], amounts)
        if not description:
            skipped += 1
            continue

        merchant, vpa = split_upi_narration(description)
        txns.append(ParsedTxn(
            posted_at=row["date"],
            amount=amount,
            direction=direction,
            source=source,
            raw_description=description,
            merchant_normalized=merchant,
            counterparty_id=vpa,
            balance_after=closing,
            extra_metadata={"parser": "hdfc_account_pdf"},
        ))

    if skipped:
        logger.info("hdfc_account_pdf: kept %d rows, skipped %d non-transaction lines",
                    len(txns), skipped)
    return txns
