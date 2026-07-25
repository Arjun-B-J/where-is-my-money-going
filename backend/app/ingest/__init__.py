"""Deterministic extraction of transactions from statement files.

No language model runs in this package. Dates, amounts and balances are parsed
by Python and regexes only — the model is never given the chance to hallucinate
a number. It sees clean structured rows afterwards, and only to categorise
them. See docs/DECISIONS.md §1.
"""
from app.ingest.loader import (
    FileLoadResult,
    LoadResult,
    load_directory,
    load_file,
    load_transactions,
)
from app.ingest.parsers import (
    UnsupportedStatementError,
    detect,
    parse_file,
    supported_formats,
)
from app.ingest.records import ParsedTxn, external_id

__all__ = [
    "FileLoadResult",
    "LoadResult",
    "ParsedTxn",
    "UnsupportedStatementError",
    "detect",
    "external_id",
    "load_directory",
    "load_file",
    "load_transactions",
    "parse_file",
    "supported_formats",
]
