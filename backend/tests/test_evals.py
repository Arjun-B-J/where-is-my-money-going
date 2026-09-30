"""The evaluation harness: labelled data, scoring, and the failure paths.

The scoring functions are tested against hand-computed values, because a metric
that is subtly wrong produces confident, wrong conclusions about models.
"""
from __future__ import annotations

import json

import pytest

from app.evals.dataset import SLICES, DatasetError, load_golden
from app.evals.metrics import (
    Prediction,
    expected_calibration_error,
    macro_f1,
    percentile,
    score,
)
from app.evals.runner import EvalAbortedError, run_arm
from app.llm.prompts import CATEGORY_NAMES
from tests.conftest import FakeLLM


def _p(gold, category, confidence=0.9, slice_="clear_merchant", source="llm", ms=100):
    return Prediction("x", slice_, tuple(gold), category, confidence, ms, source)


# ---- the labelled set --------------------------------------------------------


def test_golden_set_loads_and_every_label_is_in_the_taxonomy():
    rows = load_golden()
    assert len(rows) >= 150
    assert len({row.id for row in rows}) == len(rows)
    assert {row.slice for row in rows} == set(SLICES)
    for row in rows:
        assert set(row.gold) <= set(CATEGORY_NAMES), row.id


def test_rows_are_normalised_like_a_real_statement():
    """The classifier must see what a parser would hand it, not the raw file."""
    row = next(r for r in load_golden() if r.id == "le01")
    txn = row.to_transaction()
    assert txn.merchant_normalized == "BUNDL TECHNOLOGIES PVT LTD"
    assert txn.counterparty_id == "bundl@okbank"


def test_label_outside_the_taxonomy_is_rejected(tmp_path):
    """An unpredictable label would silently cap accuracy and blame the model."""
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({
        "id": "x1", "slice": "opaque", "description": "POS SHOP", "amount": 10,
        "direction": "debit", "source": "bank", "gold": ["food_and_dining"],
    }) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="not in the taxonomy"):
        load_golden(path)


def test_duplicate_ids_are_rejected(tmp_path):
    line = json.dumps({
        "id": "x1", "slice": "opaque", "description": "POS SHOP", "amount": 10,
        "direction": "debit", "source": "bank", "gold": ["uncategorized"],
    })
    path = tmp_path / "dup.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="duplicate"):
        load_golden(path)


# ---- metrics -----------------------------------------------------------------


def test_no_answer_is_scored_as_wrong_not_dropped():
    predictions = [_p(["food"], "food"), _p(["food"], None, None, source="none")]
    scores = score(predictions)
    assert scores["accuracy"] == 0.5
    assert scores["answered_rate"] == 0.5


def test_any_listed_gold_label_counts():
    assert _p(["shopping", "groceries"], "groceries").correct
    assert not _p(["shopping", "groceries"], "food").correct


def test_abstention_is_measured_in_both_directions():
    predictions = [
        _p(["uncategorized"], "uncategorized", 0.3, "opaque"),
        _p(["uncategorized"], "food", 0.6, "opaque"),
        _p(["food"], "uncategorized", 0.4),
        _p(["food"], "food"),
    ]
    scores = score(predictions)
    assert scores["abstain_recall"] == 0.5
    assert scores["false_abstain_rate"] == 0.5


def test_auto_accept_uses_the_review_threshold():
    predictions = [
        _p(["food"], "food", 0.95),
        _p(["food"], "transport", 0.80),
        _p(["food"], "food", 0.50),
        _p(["food"], "food", 0.20),
    ]
    scores = score(predictions, threshold=0.70)
    assert scores["auto_accept_coverage"] == 0.5
    assert scores["auto_accept_precision"] == 0.5


def test_ece_is_zero_when_confidence_matches_accuracy():
    pairs = [(0.75, True), (0.75, True), (0.75, True), (0.75, False)]
    assert expected_calibration_error(pairs) == pytest.approx(0.0)


def test_ece_of_an_overconfident_model():
    """Always 1.0 confident, right half the time: off by 0.5."""
    pairs = [(1.0, True), (1.0, False)]
    assert expected_calibration_error(pairs) == pytest.approx(0.5)


def test_macro_f1_perfect_and_worst():
    assert macro_f1([_p(["food"], "food"), _p(["rent"], "rent")]) == pytest.approx(1.0)
    assert macro_f1([_p(["food"], "rent"), _p(["rent"], "food")]) == pytest.approx(0.0)


def test_percentile_is_nearest_rank():
    assert percentile([10, 20, 30, 40], 0.5) == 20
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile([], 0.5) is None


def test_scores_are_broken_down_by_slice():
    predictions = [
        _p(["food"], "food", slice_="clear_merchant"),
        _p(["food"], "rent", slice_="legal_entity"),
    ]
    by_slice = score(predictions)["by_slice"]
    assert by_slice["clear_merchant"]["accuracy"] == 1.0
    assert by_slice["legal_entity"]["accuracy"] == 0.0


# ---- runner ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_arm_needs_no_model():
    rows = load_golden()[:40]
    result = await run_arm(rows, arm="rules")
    assert len(result.predictions) == 40
    assert {p.source for p in result.predictions} <= {"rule", "none"}
    assert result.model is None


@pytest.mark.asyncio
async def test_rules_arm_misses_legal_entity_names():
    """The gap the model exists to close: rules only know brand spellings."""
    rows = [r for r in load_golden() if r.id in {"le02", "le05", "le26"}]
    result = await run_arm(rows, arm="rules")
    assert all(p.source == "none" for p in result.predictions)


@pytest.mark.asyncio
async def test_model_arm_uses_the_production_classifier():
    rows = [r for r in load_golden() if r.slice == "clear_merchant"][:10]
    llm = FakeLLM()
    result = await run_arm(rows, arm="llm", llm=llm, concurrency=2)
    assert len(result.predictions) == 10
    # One warm-up call plus one per row, all through the tagging schema.
    tagging_calls = [c for c in llm.calls if c["kind"] == "structured"]
    assert len(tagging_calls) == 11
    assert all("category" in c["schema"]["properties"] for c in tagging_calls)


@pytest.mark.asyncio
async def test_rules_then_model_sends_only_the_misses_to_the_model():
    rows = load_golden()[:30]
    rules_only = await run_arm(rows, arm="rules")
    misses = sum(p.source == "none" for p in rules_only.predictions)

    llm = FakeLLM()
    await run_arm(rows, arm="rules+llm", llm=llm)
    assert len(llm.calls) == misses + 1  # +1 warm-up


@pytest.mark.asyncio
async def test_unreachable_model_aborts_instead_of_scoring_zero():
    """A 0% from a model that never ran would read as the model being bad."""
    with pytest.raises(EvalAbortedError, match="not reachable"):
        await run_arm(load_golden()[:5], arm="llm", llm=FakeLLM(available=False))


@pytest.mark.asyncio
async def test_garbage_replies_abort_at_warm_up():
    with pytest.raises(EvalAbortedError, match="no usable answer"):
        await run_arm(load_golden()[:5], arm="llm", llm=FakeLLM(malformed=True))
