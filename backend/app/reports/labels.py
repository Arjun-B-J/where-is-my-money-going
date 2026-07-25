"""Human-readable labels for sources and payees.

Neither is hardcoded. Sources get generic labels, so nothing here identifies a
bank or an account, and payee notes come from the `merchant_notes` table, which
belongs to the user. Facts about one person's life are data, not code.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Collection

from sqlalchemy.orm import Session

from app.models import MerchantNote, Person, TxnSource

# Deliberately says nothing about which institution a source is. "Credit card"
# is all the report needs; which bank issued it is the reader's business.
SOURCE_LABELS: dict[str, str] = {
    TxnSource.BANK.value: "Bank account",
    TxnSource.BANK_SECONDARY.value: "Second bank account",
    TxnSource.CARD.value: "Credit card",
    TxnSource.CARD_SECONDARY.value: "Second credit card",
    TxnSource.UPI.value: "UPI app",
    TxnSource.WALLET.value: "Wallet",
    TxnSource.RECEIPT.value: "Scanned receipts",
    TxnSource.OTHER.value: "Other",
}


def source_label(source: TxnSource | str) -> str:
    key = source.value if isinstance(source, TxnSource) else str(source)
    return SOURCE_LABELS.get(key, key.replace("_", " ").title())


class PayeeAnnotator:
    """Looks up the "why notable" note for a payee name.

    Built once per report so that a table of 20 payees costs one query rather
    than 20. Matching is case-insensitive substring, lowest priority number
    first, so a specific note can be made to win over a general one.
    """

    def __init__(self, notes: list[tuple[str, str]]) -> None:
        self._notes = notes

    @classmethod
    def from_db(cls, db: Session) -> PayeeAnnotator:
        rows = (
            db.query(MerchantNote)
            .order_by(MerchantNote.priority.asc(), MerchantNote.id.asc())
            .all()
        )
        return cls([(row.pattern.upper(), row.note) for row in rows])

    def note_for(self, payee: str, *, txn_count: int = 0) -> str:
        """Return a short annotation, or a frequency remark, or empty string."""
        haystack = payee.upper()
        for pattern, note in self._notes:
            if pattern and pattern in haystack:
                return note
        # Nothing configured. High transaction counts are still worth surfacing
        # because they are a fact about the data, not a fact about the person.
        if txn_count >= 20:
            return f"High frequency — {txn_count} transactions"
        return ""


@functools.lru_cache(maxsize=1)
def _person_name_pattern() -> re.Pattern[str]:
    """Two to four capitalised words: the shape of a name in a bank narration."""
    return re.compile(r"^[A-Z][A-Z.]*(?:\s+[A-Z][A-Z.]*){1,3}$")


# Words that mean the payee is a business, however name-like it otherwise reads.
_CORPORATE_WORDS = frozenset({
    "LTD", "LIMITED", "PVT", "PRIVATE", "INC", "LLP", "CORP", "COMPANY", "CO",
    "TECHNOLOGIES", "TECHNOLOGY", "SERVICES", "SOLUTIONS", "SYSTEMS", "STORE",
    "STORES", "POS", "ATM", "UPI", "NEFT", "IMPS", "PAYMENT", "PAYMENTS",
    "SALARY", "PAYROLL", "CARD", "CREDIT", "DEBIT", "BANK", "FUEL", "STATION",
    "SUPERMARKET", "MARKETPLACE", "PHARMACY", "CINEMA", "METRO", "RAIL",
    "STREAMING", "MUSIC", "FITNESS", "CLUB", "GROCERY", "COFFEE", "ROASTERS",
    "DELIVERY", "CABS", "TAXI", "BOARD", "ELECTRICITY", "BROKING", "EMPLOYER",
    "WITHDRAWAL", "RENT", "EMI", "FASHION", "SPORTS", "CAFETERIA", "HOME",
})


def looks_like_individual(payee: str) -> bool:
    """True when a payee name looks like a person rather than a business.

    Used only to decide whether to show a name in full or reduce it to initials.
    It is intentionally biased towards *over*-detecting people: wrongly
    abbreviating a shop name in terminal output costs nothing, while failing to
    abbreviate a real person's name is the mistake worth avoiding.
    """
    stripped = " ".join(payee.strip().split())
    if not _person_name_pattern().match(stripped):
        return False
    return not any(word.strip(".") in _CORPORATE_WORDS for word in stripped.split())


def initials(payee: str) -> str:
    """`"SOME PERSON NAME"` -> `"S. P. N."`."""
    letters = [part[0] for part in payee.split() if part]
    return " ".join(f"{letter}." for letter in letters) or payee


def redact_payee(payee: str, known_people: Collection[str] | None = None) -> str:
    """Reduce a person's name to initials; leave business names as they are.

    Used by CLI output, so that a screenshot of your own spending does not expose
    the people you pay. See docs/PRIVACY.md for why that matters.

    Two signals, in order of reliability:

    1. `known_people` — names the friend detector has actually created Person rows
       for. Exact, no guessing.
    2. `looks_like_individual` — a shape heuristic, for people who were never
       detected because money only ever went one way (a landlord, say).

    The heuristic over-redacts: a two-word business name with no corporate suffix
    reads as a person. Abbreviating a shop's name in terminal output is a
    non-event; failing to abbreviate someone's name is the bug this prevents.
    """
    if known_people and payee.strip().upper() in {name.strip().upper() for name in known_people}:
        return initials(payee)
    if looks_like_individual(payee):
        return initials(payee)
    return payee


def known_person_names(db: Session) -> set[str]:
    """Every name and alias the friend detector has recorded."""
    names: set[str] = set()
    for person in db.query(Person).all():
        names.add(person.name)
        names.update(person.aliases or [])
    return names
