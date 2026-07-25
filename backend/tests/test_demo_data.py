"""The synthetic dataset: determinism and internal consistency."""
from __future__ import annotations

import itertools
from datetime import datetime

from app.demo.generator import generate_transactions
from app.models import TxnDirection, TxnSource

END = datetime(2026, 7, 1, 12, 0)


def test_deterministic_for_a_seed():
    first = generate_transactions(seed=42, months=6, end_date=END)
    second = generate_transactions(seed=42, months=6, end_date=END)

    assert [t.external_id for t in first] == [t.external_id for t in second]


def test_different_seeds_differ():
    a = generate_transactions(seed=1, months=6, end_date=END)
    b = generate_transactions(seed=2, months=6, end_date=END)
    assert [t.external_id for t in a] != [t.external_id for t in b]


def test_sorted_oldest_first():
    txns = generate_transactions(seed=42, months=6, end_date=END)
    assert txns == sorted(txns, key=lambda t: t.posted_at)


def test_covers_the_requested_window_without_month_drift():
    """Calendar months, not 30-day steps.

    Stepping by 30 days drifts by roughly five days a year, which eventually
    produces two buckets for the same month.
    """
    txns = generate_transactions(seed=42, months=12, end_date=END)
    months = {t.posted_at.strftime("%Y-%m") for t in txns}
    assert len(months) == 12


def test_amounts_are_positive():
    for txn in generate_transactions(seed=42, months=3, end_date=END):
        assert txn.amount > 0


def test_running_balance_follows_the_timeline():
    """balance_after must be consistent with the sorted order of transactions.

    Direction inference for real account statements works by diffing the closing
    balance, so demo data with an incoherent balance column would not exercise it.
    """
    txns = [
        t for t in generate_transactions(seed=42, months=6, end_date=END)
        if t.balance_after is not None
    ]
    assert len(txns) > 20

    for previous, current in itertools.pairwise(txns):
        delta = current.balance_after - previous.balance_after
        expected = (
            current.amount if current.direction == TxnDirection.CREDIT else -current.amount
        )
        assert abs(delta - expected) < 0.02, (
            f"balance jumped {delta} for a {current.direction.value} of {current.amount}"
        )


def test_only_account_statements_carry_a_balance():
    """Card and UPI exports have no balance column in reality."""
    for txn in generate_transactions(seed=42, months=3, end_date=END):
        if txn.source in {TxnSource.CARD, TxnSource.UPI}:
            assert txn.balance_after is None


def test_contains_both_directions_and_several_sources():
    txns = generate_transactions(seed=42, months=6, end_date=END)

    assert {t.direction for t in txns} == {TxnDirection.DEBIT, TxnDirection.CREDIT}
    assert len({t.source for t in txns}) >= 3


def test_contains_two_way_flows_for_friend_detection():
    txns = generate_transactions(seed=42, months=12, end_date=END)
    by_vpa: dict[str, set[TxnDirection]] = {}
    for txn in txns:
        if txn.counterparty_id:
            by_vpa.setdefault(txn.counterparty_id, set()).add(txn.direction)

    bidirectional = [vpa for vpa, directions in by_vpa.items() if len(directions) == 2]
    assert len(bidirectional) >= 3, "friend detection needs two-way counterparties"


def test_contains_emi_instalments():
    txns = generate_transactions(seed=42, months=12, end_date=END)
    instalments = [t for t in txns if "emi_instalment" in t.extra_metadata]
    assert len(instalments) >= 6

    numbers = sorted(t.extra_metadata["emi_instalment"] for t in instalments)
    assert numbers == list(range(1, len(numbers) + 1)), "instalments should be sequential"


def test_contains_opaque_payees_so_review_is_exercised():
    txns = generate_transactions(seed=42, months=12, end_date=END)
    assert any(t.extra_metadata.get("opaque") for t in txns)


def test_every_row_is_marked_synthetic():
    """Anything from this module must be identifiable as fake in the database."""
    for txn in generate_transactions(seed=42, months=2, end_date=END):
        assert txn.extra_metadata.get("synthetic") is True
