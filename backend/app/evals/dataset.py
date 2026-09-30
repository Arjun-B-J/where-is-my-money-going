"""The labelled evaluation set.

`data/golden_v1.jsonl` holds realistic Indian bank and card narrations with the
category a careful person would assign. It is a stress set, not a sample of one
person's spending: it over-represents the cases that break classifiers, so the
per-slice numbers matter more than the overall one.

| slice          | what it tests                                                  |
|----------------|----------------------------------------------------------------|
| clear_merchant | a brand name in plain text                                     |
| legal_entity   | the merchant appears only as its registered company name       |
| gateway_masked | a payment-gateway prefix (`RAZ*`, `PYU*`, `BILLDESK*`) in front |
| structural     | salary, ATM codes, EMIs, card bills, SIPs, bank charges        |
| credit         | refunds, cashback, interest, dividends: money in, not spending |
| person         | transfers to and from individuals, with and without a remark   |
| opaque         | nothing in the narration says what the payment was for        |
| ambiguous      | more than one category is defensible; `gold` lists them all    |

`gold` is a list because some rows genuinely have two right answers (Amazon is
shopping or groceries). A prediction is correct when it is any of them. Where a
row has one right answer the list has one entry, and the choice follows the
taxonomy text in `app.llm.prompts`, including its rule that a credit is never a
spending category.

Every person in the file is unmistakably fictional and every UPI handle uses the
`@okbank` placeholder, for the same reason the demo dataset does. The privacy
tests scan this file along with the source.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.ingest.normalize import split_upi_narration
from app.llm.prompts import CATEGORY_NAMES
from app.memory import Example
from app.models import Transaction, TxnDirection, TxnSource

DATA_DIR = Path(__file__).parent / "data"
GOLDEN_PATH = DATA_DIR / "golden_v1.jsonl"
PERSONAL_PATH = DATA_DIR / "personal_v1.jsonl"

SLICES = (
    "clear_merchant", "legal_entity", "gateway_masked", "structural",
    "credit", "person", "opaque", "ambiguous",
)

# `personal_v1.jsonl` tests learning from corrections (app.memory). Its
# `history` rows play the part of categories the user set by hand; the scored
# rows are the same payees paid again (`repeat_payee`), the same payees paid
# differently (`payee_variant`: a new UPI handle, or IMPS with no handle), and
# strangers with similar names (`lookalike`), where copying a past label is the
# error being measured.
PERSONAL_SLICES = ("repeat_payee", "payee_variant", "lookalike")

# Any fixed date works: the classifier never sees it. Fixed so a row's identity
# hash, and therefore everything derived from it, is reproducible.
_EVAL_DATE = datetime(2026, 9, 1, 12, 0)


@dataclass(frozen=True)
class GoldenRow:
    id: str
    slice: str
    description: str
    amount: float
    direction: TxnDirection
    source: TxnSource
    gold: tuple[str, ...]
    note: str = ""
    # "eval" rows are scored. "history" rows (personal set only) stand in for
    # categories the user set by hand, and are never scored.
    role: str = "eval"

    def is_correct(self, category: str | None) -> bool:
        return category is not None and category in self.gold

    @property
    def expects_abstention(self) -> bool:
        """The only right answer is "I cannot tell"."""
        return self.gold == ("uncategorized",)

    def to_transaction(self) -> Transaction:
        """A transient row shaped exactly as a statement parser would shape it.

        The merchant and counterparty fields come from the production
        normaliser, so the classifier sees what it would see in a real run.
        Never added to a session.
        """
        merchant, vpa = split_upi_narration(self.description)
        return Transaction(
            external_id=self.id,
            posted_at=_EVAL_DATE,
            amount=self.amount,
            direction=self.direction,
            source=self.source,
            raw_description=self.description,
            merchant_normalized=merchant,
            counterparty_id=vpa,
            extra_metadata={"eval_id": self.id},
        )


class DatasetError(ValueError):
    """The labelled file is malformed. Raised at load time, never mid-run."""


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenRow]:
    """Load and validate the labelled rows.

    Validation is strict on purpose. A label that is not in the taxonomy can
    never be predicted, so it would silently cap accuracy below 100% and read
    as a model weakness.
    """
    return _load(path, SLICES)


def load_personal(path: Path = PERSONAL_PATH) -> tuple[list[GoldenRow], list[GoldenRow]]:
    """`(history, scored)` rows of the learning-from-corrections set."""
    rows = _load(path, PERSONAL_SLICES)
    return (
        [row for row in rows if row.role == "history"],
        [row for row in rows if row.role == "eval"],
    )


def _load(path: Path, allowed_slices: tuple[str, ...]) -> list[GoldenRow]:
    rows: list[GoldenRow] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise DatasetError(f"{path.name}:{number}: not JSON ({e})") from e

        row_id = raw.get("id", "")
        if not row_id or row_id in seen:
            raise DatasetError(f"{path.name}:{number}: missing or duplicate id {row_id!r}")
        seen.add(row_id)

        gold = tuple(raw.get("gold") or ())
        unknown = [label for label in gold if label not in CATEGORY_NAMES]
        if not gold or unknown:
            raise DatasetError(f"{row_id}: gold labels {unknown or gold} are not in the taxonomy")
        role = raw.get("role", "eval")
        if role not in ("eval", "history"):
            raise DatasetError(f"{row_id}: unknown role {role!r}")
        slice_ = raw.get("slice", "history" if role == "history" else None)
        if role == "eval" and slice_ not in allowed_slices:
            raise DatasetError(f"{row_id}: unknown slice {slice_!r}")

        rows.append(GoldenRow(
            id=row_id,
            slice=str(slice_),
            description=raw["description"],
            amount=float(raw["amount"]),
            direction=TxnDirection(raw["direction"]),
            source=TxnSource(raw["source"]),
            gold=gold,
            note=raw.get("note", ""),
            role=role,
        ))
    return rows


def as_example(row: GoldenRow) -> Example:
    """A history row as the memory would hold it: shaped like a stored transaction."""
    txn = row.to_transaction()
    return Example(
        description=txn.raw_description,
        direction=txn.direction.value,
        amount=txn.amount,
        category=row.gold[0],
        counterparty=txn.counterparty_id,
        merchant=txn.merchant_normalized,
    )


def dataset_fingerprint(path: Path = GOLDEN_PATH) -> str:
    """Short content hash, recorded with every result.

    Two results are only comparable when they were scored against the same
    labels, and a filename does not guarantee that.
    """
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
