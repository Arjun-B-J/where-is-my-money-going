"""Detector behaviour. All deterministic — no model involved."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Transaction, TxnDirection, TxnSource
from app.services.subscriptions import detect_subscriptions

START = datetime(2026, 1, 12, 12, 0)


def _monthly(
    db,
    payee: str,
    amount: float,
    *,
    months: int = 6,
    category: str = "subscriptions",
    jitter: float = 0.0,
    source: TxnSource = TxnSource.CARD,
    person_id: int | None = None,
) -> None:
    """Add a charge repeating on the same day each month."""
    for index in range(months):
        posted = START + timedelta(days=30 * index)
        db.add(Transaction(
            external_id=f"{payee}-{index}",
            posted_at=posted,
            amount=amount + jitter * index,
            direction=TxnDirection.DEBIT,
            source=source,
            raw_description=f"POS {payee}",
            merchant_normalized=payee,
            category=category,
            person_id=person_id,
        ))
    db.commit()


def test_detects_a_monthly_charge(db):
    _monthly(db, "EXAMPLE STREAMING", 649)

    subs = detect_subscriptions(db)

    assert len(subs) == 1
    assert subs[0].cadence == "monthly"
    assert subs[0].median_amount == 649
    assert subs[0].occurrences == 6
    assert subs[0].annual_estimate == pytest.approx(649 * 12)


def test_detects_services_with_no_known_brand(db):
    """The point of cadence-based detection.

    The previous version matched a list of fifteen brand regexes, so a regional
    service or a local gym was invisible no matter how regularly it charged.
    """
    _monthly(db, "NEIGHBOURHOOD TIFFIN SERVICE", 3_200, category="food")

    subs = detect_subscriptions(db)
    assert len(subs) == 1
    assert subs[0].branded is False
    # Keeps its own name rather than being forced into a known label.
    assert "Tiffin" in subs[0].service


def test_brand_label_is_applied_when_recognised(db):
    _monthly(db, "NETFLIX", 649)

    subs = detect_subscriptions(db)
    assert subs[0].service == "Netflix"
    assert subs[0].branded is True


@pytest.mark.parametrize("category", ["rent", "loan_repayment", "investments", "salary"])
def test_recurring_commitments_are_not_subscriptions(db, category):
    """Rent, a card bill and a SIP are monthly and stable, and are not subscriptions.

    Counting them would report an annual "subscription cost" several times larger
    than anything the user actually subscribes to.
    """
    _monthly(db, "SOMETHING REGULAR", 32_000, category=category)

    assert detect_subscriptions(db) == []


def test_standing_transfers_to_people_are_excluded(db):
    """A monthly transfer to someone you know belongs in the People view."""
    from app.models import Person

    person = Person(name="A PERSON", relationship_type="friend")
    db.add(person)
    db.flush()
    _monthly(db, "A PERSON", 5_000, category="food", person_id=person.id)

    assert detect_subscriptions(db) == []


def test_variable_amounts_are_not_subscriptions(db):
    """A shop you visit monthly for a different amount each time is not a service."""
    _monthly(db, "EXAMPLE SUPERMARKET", 500, jitter=900, category="groceries")

    assert detect_subscriptions(db) == []


def test_utility_bills_survive_moderate_variation(db):
    """Electricity varies with usage but is still a recurring charge."""
    _monthly(db, "STATE ELECTRICITY BOARD", 2_400, jitter=90, category="utilities")

    subs = detect_subscriptions(db)
    assert len(subs) == 1
    assert subs[0].service == "Electricity"


def test_two_occurrences_are_not_a_cadence(db):
    """Two points make a line through any pair of dates."""
    _monthly(db, "EXAMPLE STREAMING", 649, months=2)

    assert detect_subscriptions(db) == []


def test_irregular_gaps_are_not_a_cadence(db):
    """Same payee, same amount, arbitrary dates: not a subscription."""
    for index, offset in enumerate([0, 3, 47, 51, 120]):
        db.add(Transaction(
            external_id=f"irregular-{index}",
            posted_at=START + timedelta(days=offset),
            amount=649,
            direction=TxnDirection.DEBIT,
            source=TxnSource.CARD,
            raw_description="POS EXAMPLE THING",
            merchant_normalized="EXAMPLE THING",
            category="entertainment",
        ))
    db.commit()

    assert detect_subscriptions(db) == []


def test_same_service_on_two_cards_is_two_rows(db):
    """Two cards charged for the same thing is two things to cancel."""
    _monthly(db, "EXAMPLE STREAMING", 649, source=TxnSource.CARD)
    for index in range(6):
        db.add(Transaction(
            external_id=f"second-card-{index}",
            posted_at=START + timedelta(days=30 * index),
            amount=649,
            direction=TxnDirection.DEBIT,
            source=TxnSource.CARD_SECONDARY,
            raw_description="POS EXAMPLE STREAMING",
            merchant_normalized="EXAMPLE STREAMING",
            category="subscriptions",
        ))
    db.commit()

    subs = detect_subscriptions(db)
    assert len(subs) == 2
    assert {sub.source for sub in subs} == {"card", "card_2"}


# ---- internal transfers ------------------------------------------------------


def _bank_debit(db, description: str, amount: float, category: str) -> None:
    db.add(Transaction(
        external_id=f"bank-{description}-{amount}",
        posted_at=START,
        amount=amount,
        direction=TxnDirection.DEBIT,
        source=TxnSource.BANK,
        raw_description=description,
        merchant_normalized=description,
        category=category,
    ))
    db.commit()


def test_card_bill_payment_is_an_internal_transfer(db):
    """Paying a card bill moves money between accounts you own.

    Both sides of it are in the dataset, so counting the bank debit as spending
    double-counts every card purchase it settles. That produced a dashboard
    reporting a deficit that did not exist, and an insight card confidently
    explaining it.
    """
    from app.services.cross_source import is_internal_transfer

    _bank_debit(db, "ACH-D-CREDIT CARD AUTO PAY", 50_000, "loan_repayment")
    txn = db.query(Transaction).one()

    assert is_internal_transfer(txn) is True


def test_a_real_emi_is_not_an_internal_transfer(db):
    """An instalment on a purchase is real spending, not a transfer."""
    from app.services.cross_source import is_internal_transfer

    _bank_debit(db, "EMI EXAMPLE ELECTRONICS 3/9", 4_499, "loan_repayment")
    assert is_internal_transfer(db.query(Transaction).one()) is False


def test_card_side_rows_are_never_internal_transfers(db):
    """Only the bank side of the pair is excluded; the purchases are the spending."""
    from app.services.cross_source import is_internal_transfer

    db.add(Transaction(
        external_id="card-side",
        posted_at=START,
        amount=50_000,
        direction=TxnDirection.CREDIT,
        source=TxnSource.CARD,
        raw_description="PAYMENT RECEIVED",
        category="loan_repayment",
    ))
    db.commit()
    assert is_internal_transfer(db.query(Transaction).one()) is False


def test_dashboard_totals_reconcile(db):
    """spend + internal transfers == total_debit, and the category split sums to spend."""
    from app.services.analytics import dashboard_summary

    _bank_debit(db, "ACH-D-CREDIT CARD AUTO PAY", 50_000, "loan_repayment")
    _bank_debit(db, "UPI-PEP GUARDIOLA-RENT", 32_000, "rent")
    _monthly(db, "EXAMPLE SUPERMARKET", 1_200, months=3, category="groceries")

    summary = dashboard_summary(db, months=12)

    assert summary.spend + summary.internal_transfers == pytest.approx(summary.total_debit)
    category_total = sum(row.total for row in summary.by_category)
    assert category_total == pytest.approx(summary.spend), (
        "the category breakdown must add up to the headline spend figure"
    )
    assert all("CREDIT CARD" not in row.merchant for row in summary.top_merchants), (
        "a card-bill payment is not a merchant"
    )
