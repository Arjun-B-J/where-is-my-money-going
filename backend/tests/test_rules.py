"""The deterministic rule engine."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.models import TagSource, Transaction, TxnDirection, TxnSource
from app.rules.engine import RuleEngine
from app.seed import seed_all


@pytest.fixture
def engine(db) -> RuleEngine:
    seed_all(db)
    return RuleEngine(db)


def _add(db, description: str, **overrides) -> Transaction:
    direction = overrides.get("direction", TxnDirection.DEBIT)
    txn = Transaction(
        external_id=f"test-{description[:20]}-{overrides.get('amount', 0)}-{direction.value}",
        posted_at=overrides.get("posted_at", datetime(2026, 4, 3, 12, 0)),
        amount=overrides.get("amount", 500.0),
        direction=direction,
        source=overrides.get("source", TxnSource.BANK),
        raw_description=description,
        merchant_normalized=overrides.get("merchant"),
    )
    db.add(txn)
    db.flush()
    return txn


@pytest.mark.parametrize("description,expected", [
    ("POS SWIGGY BANGALORE", "food"),
    ("UPI-ZOMATO-zomato@okbank", "food"),
    ("POS BLINKIT", "groceries"),
    ("UPI-UBER INDIA-uber@okbank", "transport"),
    ("ATM-WDL-BRANCH 0234", "cash"),
    ("POS NETFLIX", "subscriptions"),
    ("POS AMAZON", "shopping"),
    ("BBPS STATE ELECTRICITY BOARD", "utilities"),
    ("BBPS EXAMPLE FIBERNET BROADBAND", "utilities"),
    ("ACH-D-EXAMPLE BROKING-SIP INDEX FUND", "investments"),
    ("EMI Principal Amount Amortization - 3/9", "loan_repayment"),
    ("POS EXAMPLE PHARMACY", "health"),
    ("UPI-OFFICE CAFETERIA-cafe@okbank", "food"),
])
def test_matches_expected_category(engine, db, description, expected):
    txn = _add(db, description)
    assert engine.apply(txn) is True
    assert txn.category == expected
    assert txn.tag_source == TagSource.RULE
    assert txn.tag_confidence == 1.0


def test_salary_rule_requires_a_credit(engine, db):
    credit = _add(db, "NEFT-CR-EXAMPLE EMPLOYER PVT LTD-SALARY",
                  direction=TxnDirection.CREDIT, amount=120_000)
    assert engine.apply(credit) is True
    assert credit.category == "salary"

    debit = _add(db, "NEFT-CR-EXAMPLE EMPLOYER PVT LTD-SALARY",
                 direction=TxnDirection.DEBIT, amount=120_000)
    assert debit.category != "salary"


def test_unknown_payee_is_left_alone(engine, db):
    """The rule engine must decline rather than guess — that is the model's job."""
    txn = _add(db, "UPI-A PERSON-aperson@okbank")
    assert engine.apply(txn) is False
    assert txn.category is None
    assert txn.tag_source is None


def test_lower_priority_number_wins(engine, db):
    """A card-bill payment is a repayment, not shopping, even if both match."""
    txn = _add(db, "ACH-D-CREDIT CARD AUTO PAY AMAZON PAY", amount=38_000)
    assert engine.apply(txn) is True
    assert txn.category == "loan_repayment"


def test_loan_categories_set_the_loan_flag(db):
    seed_all(db)
    from app.models import Rule

    db.add(Rule(name="Test lend", pattern="(?i)LENT TO", category="loan_given", priority=1))
    db.commit()

    txn = _add(db, "UPI-LENT TO SOMEONE")
    assert RuleEngine(db).apply(txn) is True
    assert txn.category == "loan_given"
    assert txn.is_loan is True


def test_malformed_rule_is_skipped_not_fatal(db):
    """One bad regex must not take the whole pipeline down."""
    seed_all(db)
    from app.models import Rule

    db.add(Rule(name="Broken", pattern="(unclosed", category="food", priority=1))
    db.commit()

    txn = _add(db, "POS SWIGGY BANGALORE")
    assert RuleEngine(db).apply(txn) is True
    assert txn.category == "food"


def test_amount_bounds_respected(db):
    seed_all(db)
    from app.models import Rule

    db.add(Rule(name="Big rent", pattern="(?i)RENT", amount_min=25_000,
                amount_max=40_000, category="rent", priority=1))
    db.commit()
    engine = RuleEngine(db)

    inside = _add(db, "UPI-SOMEONE-RENT", amount=32_000)
    assert engine.apply(inside) is True
    assert inside.category == "rent"

    outside = _add(db, "UPI-SOMEONE-RENT", amount=500)
    assert engine.apply(outside) is False


def test_seed_is_idempotent(db):
    first = seed_all(db)
    second = seed_all(db)

    assert first["categories"] > 0
    assert second == {"categories": 0, "rules": 0, "merchant_notes": 0}


def test_seeded_categories_match_the_model_taxonomy(db):
    """The prompt's enum and the database must not drift apart."""
    from app.llm.prompts import CATEGORY_NAMES
    from app.models import Category

    seed_all(db)
    stored = {row.name for row in db.query(Category).all()}
    assert stored == set(CATEGORY_NAMES)
