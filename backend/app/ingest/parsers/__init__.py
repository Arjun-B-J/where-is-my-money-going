"""Statement parsers and the registry that picks one.

Selection is by **file content**, not filename. An earlier version matched on
substrings like `"billedstatement"`, which only worked for one person's
download naming and silently fell through to the wrong parser for everyone
else. Each parser declares a signature that must appear in the document's first
page of text.

Adding a bank means writing one module with a `parse()` function and appending
one `Parser` entry here.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.ingest.parsers import hdfc_account_pdf, hdfc_credit_csv, hdfc_credit_pdf
from app.ingest.records import ParsedTxn
from app.models import TxnSource

logger = logging.getLogger(__name__)

# How much of the document to read when sniffing. Enough for a header block.
_SAMPLE_CHARS = 4000


class UnsupportedStatementError(ValueError):
    """Raised when no registered parser recognises a file."""


@dataclass(frozen=True)
class Parser:
    name: str
    label: str
    extensions: tuple[str, ...]
    signature: re.Pattern[str]
    default_source: TxnSource
    parse: Callable[..., list[ParsedTxn]]


REGISTRY: tuple[Parser, ...] = (
    Parser(
        name="hdfc_credit_csv",
        label="HDFC credit card (CSV export)",
        extensions=(".csv",),
        # The export's own column header. Present in every version of the file.
        signature=re.compile(r"Transaction\s*Details", re.IGNORECASE),
        default_source=TxnSource.CARD,
        parse=hdfc_credit_csv.parse,
    ),
    Parser(
        name="hdfc_credit_pdf",
        label="HDFC credit card (PDF statement)",
        extensions=(".pdf",),
        # The date|time pipe format is unique to the card statement layout.
        signature=re.compile(r"\d{2}/\d{2}/\d{4}\|\s*\d{2}:\d{2}"),
        default_source=TxnSource.CARD,
        parse=hdfc_credit_pdf.parse,
    ),
    Parser(
        name="hdfc_account_pdf",
        label="HDFC savings account (PDF statement)",
        extensions=(".pdf",),
        signature=re.compile(r"Narration|Closing\s*Balance|Withdrawal\s*Amt", re.IGNORECASE),
        default_source=TxnSource.BANK,
        parse=hdfc_account_pdf.parse,
    ),
)


def _sample_text(path: Path) -> str:
    """Read a short text sample for signature matching."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    return ""
                return (pdf.pages[0].extract_text() or "")[:_SAMPLE_CHARS]
        except Exception as e:
            logger.warning("Could not read %s for detection: %s", path.name, e)
            return ""
    if suffix in {".csv", ".txt"}:
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                return f.read(_SAMPLE_CHARS)
        except OSError as e:
            logger.warning("Could not read %s for detection: %s", path.name, e)
            return ""
    return ""


def detect(path: str | Path) -> Parser | None:
    """Return the parser whose signature matches this file, or None."""
    path = Path(path)
    suffix = path.suffix.lower()
    candidates = [p for p in REGISTRY if suffix in p.extensions]
    if not candidates:
        return None

    sample = _sample_text(path)
    for parser in candidates:
        if parser.signature.search(sample):
            return parser
    return None


def parse_file(path: str | Path, source: TxnSource | None = None) -> list[ParsedTxn]:
    """Parse one statement file.

    `source` overrides the parser's default, which is how a second card or a
    second bank account gets its own bucket.
    """
    path = Path(path)
    parser = detect(path)
    if parser is None:
        supported = ", ".join(sorted({p.label for p in REGISTRY}))
        raise UnsupportedStatementError(
            f"{path.name}: no parser recognised this file. Supported: {supported}."
        )
    logger.info("Parsing %s with %s", path.name, parser.name)
    return parser.parse(path, source or parser.default_source)


def supported_formats() -> list[dict[str, str]]:
    """Describe the registry for the API and the UI's upload hint."""
    return [
        {"name": p.name, "label": p.label, "extensions": " ".join(p.extensions)}
        for p in REGISTRY
    ]


__all__ = [
    "REGISTRY",
    "Parser",
    "UnsupportedStatementError",
    "detect",
    "parse_file",
    "supported_formats",
]
