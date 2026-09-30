"""Learning from the categories you set by hand.

Correcting a category used to teach the system nothing. Label a landlord's UPI
handle as rent fifty times and the fifty-first payment still came back
uncategorised, because the classifier sees every transaction alone and a
person's name says nothing about what they were paid for.

Two tiers, in order of trust:

1. **Decided.** The same UPI handle (or, on a row without one, the same merchant
   string) in the same direction, and every earlier label for it agrees. Your
   label is applied directly, with no model call: there is nothing left for a
   model to judge. `decide()`.
2. **Suggested.** Everything short of that becomes examples in the classifier's
   prompt, and the model decides: the same name under a new handle, the same
   landlord paid over IMPS (no handle at all), the same shop paid by card.
   Found by exact name and by embedding similarity. `lookup()`.

Tier 1 exists because an instruction is not a guarantee. On the personal-payee
eval a 3B model ignored an exact same-handle example half the time, and a plain
lookup beat it.

Two boundaries keep it safe (DECISIONS.md §15). Retrieval only ever influences a
label, never an amount. And a similar name is not the same payee: `ZAKIR KHAN`
is not `ZAKIR HUSSAIN`.

That second boundary needed more than an instruction in the prompt. Measured on
the personal-payee eval, EmbeddingGemma scored look-alike strangers (0.93 to
0.97) *above* the same landlord paid over IMPS (0.83 to 0.87), so no similarity
floor separates them. Similar matches therefore also pass a name check: every
word of the stored payee's name must appear in the new row. Embeddings find the
candidates; the name check verifies them, the usual split in entity resolution.
The cost is recall on abbreviated names (`KISHORE K` for `KISHORE KUMAR`), which
the eval keeps two rows to show. Those rows go to the review queue instead of
inheriting a guess, which is the side this app errs on.

Vectors are computed per run and held in memory rather than stored. A few
hundred corrections embed in one batched call, and recomputing means a
relabelled row can never leave a stale vector behind. Persisting them starts to
pay only past a few thousand corrections.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from sqlalchemy.orm import Session

from app.models import TagSource, Transaction

logger = logging.getLogger(__name__)

# Upper bound on corrections loaded per run, newest first. Far above what one
# person produces in a year of reviewing, and it keeps the batch embed bounded.
MAX_EXAMPLES = 2_000

# Reference numbers and card digits vary on every row and say nothing about the
# payee, so they are removed before embedding. Left in, two payments to the same
# person would look less alike than two payments to strangers on the same day.
_NOISE = re.compile(r"\d{4,}|X{4,}", re.IGNORECASE)

# Words of two or more letters, for the name check. Transfer rails and app
# remarks appear on every row, so they are not part of anyone's name.
_WORD = re.compile(r"[A-Z]{2,}")
_NOT_A_NAME = frozenset({
    "UPI", "IMPS", "NEFT", "RTGS", "POS", "ACH", "PAYMENT", "FROM", "PHONE", "SENT",
    "VIA", "APP", "NETBANK", "TRANSFER", "DR", "CR", "PVT", "LTD",
})


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...


@dataclass(frozen=True)
class Example:
    """One transaction you categorised by hand."""

    description: str
    direction: str
    amount: float
    category: str
    counterparty: str | None = None
    merchant: str | None = None


@dataclass(frozen=True)
class Match:
    example: Example
    similarity: float
    # How it was found: "handle" (identical UPI handle), "name" (identical payee
    # or merchant string) or "similar" (embedding neighbour that passed the
    # name check). Shown to the model, so it knows how much the example proves.
    via: str

    @property
    def exact(self) -> bool:
        return self.via != "similar"


def memory_text(description: str, direction: str) -> str:
    """The string that gets embedded, for both stored examples and queries.

    The prefix is the one EmbeddingGemma documents for symmetric similarity, so
    both sides are embedded as the same kind of text.
    """
    cleaned = " ".join(_NOISE.sub(" ", description).split())
    return f"task: sentence similarity | query: {direction} {cleaned}"


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _key(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    # Very short merchant strings ("ATM", "UPI") are shared by unrelated rows.
    return value if len(value) >= 4 else None


def _name_words(text: str | None) -> frozenset[str]:
    return frozenset(
        word for word in _WORD.findall((text or "").upper())
        if word not in _NOT_A_NAME and set(word) != {"X"}
    )


def same_name(example: Example, description: str) -> bool:
    """Every word of the stored payee's name appears in the new row.

    Order-free, so `BOND RUSKIN` still matches, and rail-free, so an IMPS row
    matches a UPI one. A stored row with no usable name cannot be checked, and
    passes.
    """
    wanted = _name_words(example.merchant)
    return not wanted or wanted <= _name_words(description)


class CorrectionMemory:
    """Your corrections, searchable by exact handle and by similarity."""

    def __init__(
        self,
        examples: Sequence[Example],
        vectors: np.ndarray | None,
        embedder: Embedder | None,
        *,
        top_k: int = 4,
        min_similarity: float = 0.80,
        name_check: bool = True,
    ) -> None:
        self.examples = list(examples)
        self._vectors = vectors
        self._embedder = embedder
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.name_check = name_check
        self._queries: dict[str, np.ndarray] = {}

        self._by_counterparty: dict[tuple[str, str], list[int]] = {}
        self._by_merchant: dict[tuple[str, str], list[int]] = {}
        for index, example in enumerate(self.examples):
            if key := _key(example.counterparty):
                self._by_counterparty.setdefault((example.direction, key), []).append(index)
            if key := _key(example.merchant):
                self._by_merchant.setdefault((example.direction, key), []).append(index)

    @property
    def similarity_available(self) -> bool:
        """False when the embedding model was unreachable: exact matches still work."""
        return self._vectors is not None

    # ---- construction ----------------------------------------------------------

    @classmethod
    async def build(
        cls,
        examples: Sequence[Example],
        embedder: Any | None,
        *,
        top_k: int = 4,
        min_similarity: float = 0.80,
        name_check: bool = True,
    ) -> CorrectionMemory:
        """Embed `examples` in one call. Degrades to exact matching if that fails."""
        vectors = None
        embed = getattr(embedder, "embed", None)
        if examples and embed is not None:
            raw = await embed([memory_text(e.description, e.direction) for e in examples])
            if raw:
                vectors = _normalise(np.asarray(raw, dtype=np.float32))
            else:
                logger.warning(
                    "Embedding model unavailable; learning from corrections falls back "
                    "to exact handle matches for this run"
                )
        return cls(
            examples, vectors, embedder if vectors is not None else None,
            top_k=top_k, min_similarity=min_similarity, name_check=name_check,
        )

    @classmethod
    async def from_db(cls, db: Session, embedder: Any | None, **kwargs: Any) -> CorrectionMemory:
        """Every row whose category you set yourself, newest first."""
        rows = (
            db.query(Transaction)
            .filter(
                Transaction.tag_source == TagSource.USER,
                Transaction.category.isnot(None),
                Transaction.is_duplicate.is_(False),
            )
            .order_by(Transaction.updated_at.desc())
            .limit(MAX_EXAMPLES)
            .all()
        )
        examples = [
            Example(
                description=row.raw_description,
                direction=row.direction.value,
                amount=row.amount,
                category=row.category or "uncategorized",
                counterparty=row.counterparty_id,
                merchant=row.merchant_normalized,
            )
            for row in rows
        ]
        return await cls.build(examples, embedder, **kwargs)

    # ---- queries ---------------------------------------------------------------

    async def prepare(self, queries: Iterable[tuple[str, str]]) -> None:
        """Embed the `(description, direction)` pairs about to be looked up, in one batch."""
        if self._embedder is None:
            return
        texts = [memory_text(description, direction) for description, direction in queries]
        missing = [t for t in dict.fromkeys(texts) if t not in self._queries]
        if not missing:
            return
        raw = await self._embedder.embed(missing)
        if not raw:
            logger.warning("Could not embed %d queries; exact matches only", len(missing))
            return
        for text, vector in zip(missing, _normalise(np.asarray(raw, dtype=np.float32)), strict=True):
            self._queries[text] = vector

    def decide(
        self,
        direction: str,
        *,
        counterparty: str | None = None,
        merchant: str | None = None,
    ) -> Example | None:
        """Your earlier label, when this is unambiguously the same payee.

        Keyed on the UPI handle when the row has one, because two people can share
        a name but not a handle. On a row without a handle (a card payment, an
        IMPS transfer) the merchant string stands in, but only against earlier
        rows that had no handle either: an NEFT payment to someone who shares
        your landlord's name is not your landlord. If your earlier labels for the
        key disagree, nothing is decided and the model sees all of them instead.
        """
        if key := _key(counterparty):
            indices = self._by_counterparty.get((direction, key), [])
        elif key := _key(merchant):
            indices = [
                i for i in self._by_merchant.get((direction, key), [])
                if not self.examples[i].counterparty
            ]
        else:
            return None
        if not indices or len({self.examples[i].category for i in indices}) != 1:
            return None
        return self.examples[indices[0]]

    def lookup(
        self,
        description: str,
        direction: str,
        *,
        counterparty: str | None = None,
        merchant: str | None = None,
    ) -> list[Match]:
        """Up to `top_k` past decisions for this payee: exact matches first."""
        chosen: list[Match] = []
        seen: set[int] = set()

        def take(index: int, similarity: float, via: str) -> None:
            if index not in seen and len(chosen) < self.top_k:
                seen.add(index)
                chosen.append(Match(self.examples[index], similarity, via))

        for table, value, via in (
            (self._by_counterparty, counterparty, "handle"),
            (self._by_merchant, merchant, "name"),
        ):
            if key := _key(value):
                for index in table.get((direction, key), []):
                    take(index, 1.0, via)

        query = self._queries.get(memory_text(description, direction))
        if self._vectors is not None and query is not None and len(chosen) < self.top_k:
            similarities = self._vectors @ query
            for index in np.argsort(-similarities):
                score = float(similarities[index])
                if score < self.min_similarity:
                    break
                example = self.examples[index]
                if example.direction != direction:
                    continue
                if self.name_check and not same_name(example, description):
                    continue
                take(int(index), score, "similar")
                if len(chosen) >= self.top_k:
                    break
        return chosen
