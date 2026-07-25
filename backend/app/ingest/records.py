"""The record every source produces, and how its identity is computed.

Statement parsers, the demo generator and the receipt scanner all emit
`ParsedTxn`. Nothing downstream needs to know which produced a given row.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from app.models import TxnDirection, TxnSource


def external_id(
    source: TxnSource | str,
    posted_at: datetime,
    amount: float,
    description: str,
) -> str:
    """Stable identity for a transaction, so re-ingesting a file is a no-op.

    Derived from the four fields a statement cannot change between exports:
    account, date, amount and description. Two genuinely distinct transactions
    that share all four — buying the same coffee twice in a day at the same
    price — collapse into one. That is a deliberate trade: silently doubling
    someone's spending totals is a worse failure than dropping a duplicate
    coffee, and bank statements rarely carry a usable unique reference.

    The date is truncated to the day because the same transaction often carries
    a different timestamp in a re-export.
    """
    src = source.value if isinstance(source, TxnSource) else str(source)
    parts = f"{src}|{posted_at.date().isoformat()}|{amount:.2f}|{description.strip().lower()}"
    return hashlib.sha1(parts.encode()).hexdigest()[:20]


@dataclass
class ParsedTxn:
    """One transaction, extracted but not yet categorised.

    Produced by deterministic code only — no model output reaches these fields.
    """

    posted_at: datetime
    amount: float                 # always positive
    direction: TxnDirection
    source: TxnSource
    raw_description: str
    merchant_normalized: str | None = None
    counterparty_id: str | None = None   # UPI VPA when the statement carries one
    balance_after: float | None = None
    extra_metadata: dict = field(default_factory=dict)
    # Parsers may supply their own; otherwise it is derived in __post_init__.
    external_id: str = ""

    def __post_init__(self) -> None:
        if self.amount < 0:
            # Sign belongs in `direction`. A negative amount here means a parser
            # leaked a statement's sign convention, so fail loudly.
            raise ValueError(
                f"ParsedTxn.amount must be positive; got {self.amount} "
                f"for {self.raw_description!r}. Encode direction in `direction`."
            )
        if not self.external_id:
            self.external_id = external_id(
                self.source, self.posted_at, self.amount, self.raw_description
            )
