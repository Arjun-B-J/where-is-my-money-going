"""Learning from corrections: lookup order, degradation, provenance, and the eval.

The embedder here is a deterministic stand-in (hashed character trigrams), so
these tests check the mechanics: exact before similar, direction respected,
failure degrading to exact matching. Whether a real embedding model finds the
right payee is the personal eval's job, not a unit test's.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np
import pytest

from app.evals.dataset import PERSONAL_SLICES, load_personal
from app.evals.runner import EvalAbortedError, run_arm
from app.memory import CorrectionMemory, Example, memory_text
from app.models import TagSource, Transaction, TxnDirection, TxnSource
from app.pipeline.nodes import PipelineState, classify_one, node_llm_tag
from tests.conftest import FakeLLM

_DIM = 256


class TrigramEmbedder:
    """Deterministic embeddings: similar strings get similar vectors."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.calls += 1
        if not self.available:
            return None
        vectors = []
        for text in texts:
            vector = np.zeros(_DIM, dtype=np.float32)
            padded = f"  {text.lower()}  "
            for i in range(len(padded) - 2):
                bucket = int(hashlib.md5(padded[i:i + 3].encode()).hexdigest(), 16) % _DIM
                vector[bucket] += 1.0
            vectors.append(vector.tolist())
        return vectors


LANDLORD = Example(
    description="UPI-RUSKIN BOND-ruskinbond@okbank-UPI", direction="debit",
    amount=30_000, category="rent", counterparty="ruskinbond@okbank", merchant="RUSKIN BOND",
)
MILKMAN = Example(
    description="UPI-AR RAHMAN-arrahman@okbank-UPI", direction="debit",
    amount=1_480, category="groceries", counterparty="arrahman@okbank", merchant="AR RAHMAN",
)
REFUND = Example(
    description="UPI-RUSKIN BOND-ruskinbond@okbank-DEPOSIT BACK", direction="credit",
    amount=60_000, category="uncategorized", counterparty="ruskinbond@okbank",
    merchant="RUSKIN BOND",
)


# ---- lookup ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_handle_is_an_exact_match_even_without_embeddings():
    memory = await CorrectionMemory.build([LANDLORD, MILKMAN], embedder=None)
    found = memory.lookup(
        "UPI-RUSKIN BOND-ruskinbond@okbank-SENT VIA APP", "debit",
        counterparty="ruskinbond@okbank",
    )
    assert [m.example.category for m in found] == ["rent"]
    assert found[0].exact


@pytest.mark.asyncio
async def test_direction_must_match():
    """Rent paid to a landlord says nothing about a deposit coming back."""
    memory = await CorrectionMemory.build([LANDLORD, REFUND], embedder=None)
    found = memory.lookup("anything", "credit", counterparty="ruskinbond@okbank")
    assert [m.example.category for m in found] == ["uncategorized"]


@pytest.mark.asyncio
async def test_similarity_finds_the_same_payee_without_a_handle():
    memory = await CorrectionMemory.build(
        [LANDLORD, MILKMAN], TrigramEmbedder(), min_similarity=0.3
    )
    query = "IMPS-612345678907-RUSKIN BOND-ICIC-XXXXXXXX4411"
    await memory.prepare([(query, "debit")])
    found = memory.lookup(query, "debit")
    assert found and found[0].example is LANDLORD
    assert not found[0].exact


@pytest.mark.asyncio
async def test_exact_matches_come_before_similar_ones_and_top_k_holds():
    examples = [LANDLORD] * 3 + [MILKMAN]
    memory = await CorrectionMemory.build(
        examples, TrigramEmbedder(), top_k=2, min_similarity=0.0
    )
    query = "UPI-RUSKIN BOND-ruskinbond@okbank-OCT"
    await memory.prepare([(query, "debit")])
    found = memory.lookup(query, "debit", counterparty="ruskinbond@okbank")
    assert len(found) == 2
    assert all(m.exact for m in found)


@pytest.mark.asyncio
async def test_a_dead_embedding_model_degrades_to_exact_matching():
    memory = await CorrectionMemory.build([LANDLORD], TrigramEmbedder(available=False))
    assert not memory.similarity_available
    query = "IMPS-612345678907-RUSKIN BOND-ICIC-XXXXXXXX4411"
    await memory.prepare([(query, "debit")])
    assert memory.lookup(query, "debit") == []
    assert memory.lookup(query, "debit", counterparty="ruskinbond@okbank")


@pytest.mark.parametrize("description,expected", [
    ("IMPS-612345678907-RUSKIN BOND-ICIC-XXXXXXXX4411", True),   # another rail
    ("UPI-BOND RUSKIN-bond.r@okbank-UPI", True),                  # order-free
    ("UPI-RUSKIN BOSE-ruskinbose@okbank-UPI", False),             # a stranger
    ("UPI-RUSKIN B-ruskin.b@okbank-UPI", False),                  # abbreviated: a known miss
])
def test_name_check(description, expected):
    from app.memory.store import same_name

    assert same_name(LANDLORD, description) is expected


@pytest.mark.asyncio
async def test_similar_but_differently_named_payees_are_not_offered():
    """The measured failure: embeddings rank look-alikes above real variants."""
    memory = await CorrectionMemory.build([LANDLORD], TrigramEmbedder(), min_similarity=0.0)
    query = "UPI-RUSKIN BOSE-ruskinbose@okbank-UPI"
    await memory.prepare([(query, "debit")])
    assert memory.lookup(query, "debit") == []
    unchecked = await CorrectionMemory.build(
        [LANDLORD], TrigramEmbedder(), min_similarity=0.0, name_check=False
    )
    await unchecked.prepare([(query, "debit")])
    assert unchecked.lookup(query, "debit")


def test_reference_numbers_do_not_reach_the_embedding():
    """Row-specific digits would make two payments to one person look unrelated."""
    text = memory_text("IMPS-612345678907-RUSKIN BOND-ICIC-XXXXXXXX4411", "debit")
    assert "612345678907" not in text
    assert "XXXXXXXX" not in text
    assert "RUSKIN BOND" in text


# ---- the classifier and the pipeline ------------------------------------------


def _txn(description: str, *, counterparty: str | None = None) -> Transaction:
    return Transaction(
        external_id=description, posted_at=datetime(2026, 9, 1, 12),
        amount=30_000.0, direction=TxnDirection.DEBIT, source=TxnSource.BANK,
        raw_description=description, merchant_normalized=None, counterparty_id=counterparty,
    )


@pytest.mark.asyncio
async def test_examples_reach_the_prompt_with_the_same_handle_marked():
    llm = FakeLLM()
    memory = await CorrectionMemory.build([LANDLORD], embedder=None)
    txn = _txn("UPI-RUSKIN BOND-ruskinbond@okbank-OCT", counterparty="ruskinbond@okbank")
    await classify_one(txn, llm, memory.lookup(
        txn.raw_description, "debit", counterparty=txn.counterparty_id,
    ))
    prompt = llm.calls[-1]["messages"][-1]["content"]
    assert "categorised these earlier transactions themselves" in prompt
    assert "same UPI handle" in prompt
    assert ": rent" in prompt


@pytest.mark.asyncio
async def test_no_examples_means_the_prompt_is_unchanged():
    llm = FakeLLM()
    await classify_one(_txn("POS EXAMPLE COFFEE ROASTERS"), llm, [])
    assert "earlier transactions" not in llm.calls[-1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_same_handle_takes_your_label_without_asking_the_model(db):
    corrected = _txn("UPI-RUSKIN BOND-ruskinbond@okbank-UPI", counterparty="ruskinbond@okbank")
    corrected.category, corrected.tag_source = "rent", TagSource.USER
    fresh = _txn("UPI-RUSKIN BOND-ruskinbond@okbank-OCT", counterparty="ruskinbond@okbank")
    db.add_all([corrected, fresh])
    db.commit()

    llm = FakeLLM()
    state: PipelineState = {"inserted_ids": [fresh.id], "timings_ms": {}}
    update = await node_llm_tag(state, db, llm)

    assert update["memory_decided"] == 1
    assert llm.calls == []
    db.refresh(fresh)
    assert (fresh.category, fresh.tag_source, fresh.needs_review) == ("rent", TagSource.MEMORY, False)


@pytest.mark.asyncio
async def test_same_name_without_a_handle_is_only_a_suggestion(db):
    """An NEFT payment to someone with your landlord's name is not your landlord."""
    corrected = _txn("UPI-RUSKIN BOND-ruskinbond@okbank-UPI", counterparty="ruskinbond@okbank")
    corrected.merchant_normalized = "RUSKIN BOND"
    corrected.category, corrected.tag_source = "rent", TagSource.USER
    fresh = _txn("NEFT DR-ICIC0001234-RUSKIN BOND-NETBANK")
    fresh.merchant_normalized = "RUSKIN BOND"
    db.add_all([corrected, fresh])
    db.commit()

    llm = FakeLLM()
    state: PipelineState = {"inserted_ids": [fresh.id], "timings_ms": {}}
    update = await node_llm_tag(state, db, llm)

    assert (update["memory_decided"], update["memory_assisted"]) == (0, 1)
    assert ", same name" in llm.calls[-1]["messages"][-1]["content"]
    db.refresh(fresh)
    assert fresh.tag_source == TagSource.LLM
    assert fresh.tag_reason and fresh.tag_reason.endswith("used your earlier labels")


@pytest.mark.asyncio
async def test_conflicting_labels_for_one_handle_decide_nothing():
    rent = LANDLORD
    repair = Example(
        description="UPI-RUSKIN BOND-ruskinbond@okbank-PLUMBER", direction="debit",
        amount=2_000, category="uncategorized", counterparty="ruskinbond@okbank",
        merchant="RUSKIN BOND",
    )
    memory = await CorrectionMemory.build([rent, repair], embedder=None)
    assert memory.decide("debit", counterparty="ruskinbond@okbank") is None
    assert len(memory.lookup("x", "debit", counterparty="ruskinbond@okbank")) == 2


# ---- the personal eval ---------------------------------------------------------


def test_personal_set_loads():
    history, rows = load_personal()
    assert len(history) == 24
    assert {row.slice for row in rows} == set(PERSONAL_SLICES)
    assert all(row.role == "history" for row in history)


@pytest.mark.asyncio
async def test_lookup_arm_gets_every_repeat_payee_and_no_variant_without_a_handle():
    history, rows = load_personal()
    result = await run_arm(rows, arm="lookup", history=history)
    by_slice: dict[str, list[bool]] = {}
    for p in result.predictions:
        by_slice.setdefault(p.slice, []).append(p.correct)
    assert all(by_slice["repeat_payee"])
    # Variants paid over IMPS or NEFT carry no UPI handle to match on.
    no_handle = {"v01", "v04", "v05", "v07", "v09", "v12"}
    assert not any(p.correct for p in result.predictions if p.row_id in no_handle)


@pytest.mark.asyncio
async def test_memory_arm_refuses_to_run_without_embeddings():
    """Otherwise it would quietly become exact matching and report that."""
    history, rows = load_personal()
    with pytest.raises(EvalAbortedError, match="no embeddings"):
        await run_arm(rows[:5], arm="llm+memory", llm=FakeLLM(), history=history)
