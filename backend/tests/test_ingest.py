"""Extraction and loading: identity, idempotency, parser detection."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.ingest import UnsupportedStatementError, detect, load_transactions, parse_file
from app.ingest.normalize import (
    extract_vpa,
    normalize_merchant,
    split_upi_narration,
)
from app.ingest.records import ParsedTxn, external_id
from app.models import Transaction, TxnDirection, TxnSource


def _txn(**overrides) -> ParsedTxn:
    defaults = {
        "posted_at": datetime(2026, 4, 3, 12, 0),
        "amount": 1_250.0,
        "direction": TxnDirection.DEBIT,
        "source": TxnSource.BANK,
        "raw_description": "POS EXAMPLE SUPERMARKET",
    }
    return ParsedTxn(**{**defaults, **overrides})


# ---- identity ---------------------------------------------------------------


def test_external_id_is_stable():
    args = (TxnSource.BANK, datetime(2026, 4, 3, 9, 30), 1250.0, "POS SHOP")
    assert external_id(*args) == external_id(*args)


def test_external_id_ignores_time_of_day():
    """Re-exported statements often carry a different timestamp for the same row."""
    morning = external_id(TxnSource.BANK, datetime(2026, 4, 3, 9, 0), 100.0, "POS SHOP")
    evening = external_id(TxnSource.BANK, datetime(2026, 4, 3, 21, 0), 100.0, "POS SHOP")
    assert morning == evening


def test_external_id_ignores_description_case_and_padding():
    a = external_id(TxnSource.BANK, datetime(2026, 4, 3), 100.0, "POS Shop ")
    b = external_id(TxnSource.BANK, datetime(2026, 4, 3), 100.0, "pos shop")
    assert a == b


@pytest.mark.parametrize("changed", [
    {"source": TxnSource.CARD},
    {"posted_at": datetime(2026, 4, 4)},
    {"amount": 1251.0},
    {"description": "POS OTHER SHOP"},
])
def test_external_id_changes_with_each_component(changed):
    base = {
        "source": TxnSource.BANK,
        "posted_at": datetime(2026, 4, 3),
        "amount": 1250.0,
        "description": "POS SHOP",
    }
    assert external_id(**base) != external_id(**{**base, **changed})


def test_negative_amount_is_rejected():
    """Sign belongs in `direction`; a negative amount means a parser leaked one."""
    with pytest.raises(ValueError, match="must be positive"):
        _txn(amount=-500.0)


# ---- loading ----------------------------------------------------------------


def test_load_inserts_rows(db):
    result = load_transactions(db, [_txn(), _txn(amount=99.0, raw_description="POS CAFE")])

    assert result.inserted == 2
    assert result.duplicates == 0
    assert db.query(Transaction).count() == 2


def test_load_skips_duplicates_across_calls(db):
    load_transactions(db, [_txn()])
    result = load_transactions(db, [_txn()])

    assert result.inserted == 0
    assert result.duplicates == 1
    assert db.query(Transaction).count() == 1


def test_load_skips_duplicates_inside_one_batch(db):
    """Overlapping statement date ranges are normal, not exceptional."""
    result = load_transactions(db, [_txn(), _txn(), _txn()])

    assert result.inserted == 1
    assert result.duplicates == 2


def test_same_transaction_on_two_accounts_is_kept(db):
    """Identity includes the source, so a linked card and wallet both keep their row."""
    result = load_transactions(db, [_txn(), _txn(source=TxnSource.CARD)])
    assert result.inserted == 2


def test_load_preserves_balance_and_metadata(db):
    load_transactions(db, [_txn(balance_after=54_321.0, extra_metadata={"parser": "x"})])
    stored = db.query(Transaction).one()

    assert stored.balance_after == 54_321.0
    assert stored.extra_metadata["parser"] == "x"


# ---- narration cleanup ------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("POS EXAMPLE SUPERMARKET", "EXAMPLE SUPERMARKET"),
    ("RAZ*SOME SHOP", "SOME SHOP"),
    ("EXAMPLE FASHION.COM/ORDER/123", "EXAMPLE FASHION"),
    # Stacked transfer-rail prefixes carry no payee information.
    ("NEFT-CR-000123456789-EMPLOYER", "EMPLOYER"),
    ("ACH-D-EXAMPLE BROKING-SIP", "EXAMPLE BROKING-SIP"),
])
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected


def test_normalize_merchant_returns_none_when_nothing_survives():
    """Better an explicit unknown than a merchant called "000000123456"."""
    assert normalize_merchant("000000123456") is None


def test_extract_vpa():
    assert extract_vpa("UPI-SOMEONE-someone@okbank-PAYMENT") == "someone@okbank"
    assert extract_vpa("POS SHOP") is None


def test_split_upi_narration_takes_the_payee_field():
    name, vpa = split_upi_narration("UPI-EXAMPLE PAYEE-payee@okbank-HDFC0001-4837-RENT")
    assert name == "EXAMPLE PAYEE"
    assert vpa == "payee@okbank"


def test_split_upi_narration_falls_back_for_other_shapes():
    name, vpa = split_upi_narration("POS EXAMPLE COFFEE ROASTERS")
    assert name == "EXAMPLE COFFEE ROASTERS"
    assert vpa is None


# ---- parser detection -------------------------------------------------------


CSV_STATEMENT = """\
Statement for card ending 0000
Some Cardholder

Transaction Details:
"Date","Sr.No.","Transaction Details","Reward Points","Intl.Amount","Amount(in Rs)","Sign"
"0000 SOME CARDHOLDER"
"03-APR-26","1","POS EXAMPLE SUPERMARKET","12","","1,299.00","1,299.00"
"05-APR-26","2","EXAMPLE STREAMING","0","","-649.00","-649.00"
"07-APR-26","3","","0","","0.00","0.00"
"""


def test_detects_card_csv_by_content(tmp_path):
    """Detection must not depend on the filename.

    The previous version matched substrings like "billedstatement", which only
    worked for one person's download naming.
    """
    path = tmp_path / "arbitrary-name-with-no-hints.csv"
    path.write_text(CSV_STATEMENT, encoding="utf-8")

    parser = detect(path)
    assert parser is not None
    assert parser.name == "hdfc_credit_csv"


def test_parses_card_csv(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(CSV_STATEMENT, encoding="utf-8")

    txns = parse_file(path)

    assert len(txns) == 2, "the zero-amount row should be skipped"
    purchase, refund = txns
    assert purchase.amount == 1299.0
    assert purchase.direction == TxnDirection.DEBIT
    assert purchase.posted_at.date() == datetime(2026, 4, 3).date()
    # A negative rupee amount is a refund or a payment towards the card.
    assert refund.amount == 649.0
    assert refund.direction == TxnDirection.CREDIT


def test_card_csv_source_can_be_overridden(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(CSV_STATEMENT, encoding="utf-8")

    txns = parse_file(path, source=TxnSource.CARD_SECONDARY)
    assert all(txn.source == TxnSource.CARD_SECONDARY for txn in txns)


def test_unknown_format_raises_with_a_useful_message(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text("this is not a statement\n", encoding="utf-8")

    with pytest.raises(UnsupportedStatementError, match="Supported"):
        parse_file(path)


def test_unsupported_extension_is_not_detected(tmp_path):
    path = tmp_path / "statement.docx"
    path.write_bytes(b"whatever")
    assert detect(path) is None
