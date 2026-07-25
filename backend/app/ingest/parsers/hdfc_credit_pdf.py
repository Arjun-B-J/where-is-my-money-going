"""Parser for HDFC credit-card PDF statements.

Transaction lines look like:

    DD/MM/YYYY| HH:MM <description> C <amount> [l|r]
    DD/MM/YYYY| HH:MM <description> + C <amount> [l|r]

`C` is the currency marker. A `+` *before* the marker means money came back —
a refund, a reversal or cashback. The trailing `l`/`r` is a column hint from
the two-column print layout and carries no meaning here.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from app.ingest.normalize import normalize_merchant
from app.ingest.records import ParsedTxn
from app.models import TxnDirection, TxnSource

logger = logging.getLogger(__name__)

_TXN = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})\|\s*(?P<time>\d{2}:\d{2})\s+"
    r"(?P<desc>.*?)\s+(?P<credit>\+\s+)?C\s+(?P<amount>\d[\d,]*\.\d{2})"
)

# Card statements list EMI conversions as "Principal Amount Amortization - 3/12".
# Captured here so the EMI detector can read instalment progress off it.
_EMI_INSTALMENT = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*$")


def parse(path: str | Path, source: TxnSource = TxnSource.CARD) -> list[ParsedTxn]:
    """Extract transactions from one credit-card statement PDF."""
    txns: list[ParsedTxn] = []

    with pdfplumber.open(Path(path)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                match = _TXN.search(line)
                if not match:
                    continue
                try:
                    posted = datetime.strptime(
                        f"{match.group('date')} {match.group('time')}", "%d/%m/%Y %H:%M"
                    )
                except ValueError:
                    continue

                description = match.group("desc").strip()
                if not description:
                    continue

                metadata: dict = {"parser": "hdfc_credit_pdf"}
                if instalment := _EMI_INSTALMENT.search(description):
                    metadata["emi_instalment"] = int(instalment.group(1))
                    metadata["emi_total"] = int(instalment.group(2))

                txns.append(ParsedTxn(
                    posted_at=posted,
                    amount=float(match.group("amount").replace(",", "")),
                    direction=(
                        TxnDirection.CREDIT if match.group("credit") else TxnDirection.DEBIT
                    ),
                    source=source,
                    raw_description=description,
                    merchant_normalized=normalize_merchant(description),
                    extra_metadata=metadata,
                ))

    logger.info("hdfc_credit_pdf: extracted %d transactions", len(txns))
    return txns
