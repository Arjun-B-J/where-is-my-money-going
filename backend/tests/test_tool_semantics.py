"""Two tool-result fixes found by the chat eval, pinned so they cannot regress.

Both were the same class of bug: a result the model could read the wrong way. A
figure check cannot catch either, because every number involved was real.
"""
from __future__ import annotations

from datetime import datetime

from app.agent.tools import execute
from app.models import Person, Transaction, TxnDirection, TxnSource


def _txn(db, day: datetime, amount: float, direction=TxnDirection.DEBIT, person=None, key=""):
    db.add(Transaction(
        external_id=f"{day:%Y%m%d}-{amount}-{direction.value}-{key}", posted_at=day,
        amount=amount, direction=direction, source=TxnSource.UPI,
        raw_description="UPI-EXAMPLE FOOD DELIVERY-food@okbank", category="food",
        person_id=person.id if person else None,
    ))


def test_people_balances_says_who_paid_whom(db):
    """`sent` was read as money the person sent the user."""
    person = Person(name="KYLIAN MBAPPE")
    db.add(person)
    db.flush()
    _txn(db, datetime(2026, 8, 1, 12), 5_000.0, TxnDirection.DEBIT, person, "a")
    _txn(db, datetime(2026, 8, 2, 12), 2_000.0, TxnDirection.CREDIT, person, "b")
    db.commit()

    entry = execute(db, "people_balances", {})["people"][0]
    assert entry["user_sent_to_them"] == 5_000.0
    assert entry["they_sent_to_user"] == 2_000.0
    assert "sent" not in entry and "received" not in entry


def test_a_period_before_the_data_is_not_reported_as_zero_spending(db):
    """A month with no statement is 'nothing recorded', not 'you spent nothing'."""
    _txn(db, datetime(2026, 8, 10, 12), 400.0)
    db.commit()

    result = execute(db, "spending_summary", {
        "group_by": "category", "start_date": "2025-02", "end_date": "2025-02",
    })
    assert result["grand_total"] == 0
    assert "outside the data" in result["coverage"]["note"]
    assert result["coverage"]["data_from"] == "2026-08-10"


def test_a_period_straddling_the_data_says_the_total_is_partial(db):
    _txn(db, datetime(2026, 8, 10, 12), 400.0)
    db.commit()

    result = execute(db, "find_transactions", {
        "start_date": "2026-07-01", "end_date": "2026-08-31", "direction": "debit",
    })
    assert result["total_amount"] == 400.0
    assert "part of this period" in result["coverage"]["note"]


def test_a_period_inside_the_data_carries_no_note(db):
    _txn(db, datetime(2026, 7, 1, 12), 100.0, key="a")
    _txn(db, datetime(2026, 8, 31, 12), 200.0, key="b")
    db.commit()

    result = execute(db, "spending_summary", {
        "group_by": "month", "start_date": "2026-07-01", "end_date": "2026-08-31",
    })
    assert "coverage" not in result
