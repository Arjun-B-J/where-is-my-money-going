"""SQLAlchemy ORM models.

All timestamps are naive UTC — see `app.clock`.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utc_now
from app.db import Base


class TxnDirection(enum.StrEnum):
    DEBIT = "debit"    # money left the account
    CREDIT = "credit"  # money arrived


class TxnSource(enum.StrEnum):
    """Which document or feed a transaction came from.

    Deliberately generic — `bank_a` rather than a bank's name — so the schema
    does not encode whose accounts these are. Human-readable labels live in
    `app.reports.labels` and are configurable.
    """

    BANK = "bank"                    # savings / current account statement
    BANK_SECONDARY = "bank_2"        # a second bank account
    CARD = "card"                    # credit-card statement
    CARD_SECONDARY = "card_2"        # a second credit card
    UPI = "upi"                      # UPI app export
    WALLET = "wallet"                # prepaid wallet
    RECEIPT = "receipt"              # scanned receipt
    OTHER = "other"


class TagSource(enum.StrEnum):
    """Who assigned the category.

    There is intentionally no value meaning "the model was asked and failed".
    When a classification attempt fails, no tag is written at all: `category`
    and `tag_source` stay NULL and `needs_review` is set. Recording a failed
    attempt as model output is the bug this enum's absence prevents.
    """

    RULE = "rule"        # deterministic regex rule
    LLM = "llm"          # local model, first pass
    VALIDATOR = "validator"  # local model, second-opinion pass overrode the first
    USER = "user"        # a human set it


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # sha1 of (source, date, amount, description) — see app.ingest.identity.
    # Makes re-ingesting the same statement a no-op.
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Always positive. Direction is a separate column so that sign conventions
    # differing between statement formats cannot leak into the data.
    amount: Mapped[float] = mapped_column(Float)
    direction: Mapped[TxnDirection] = mapped_column(Enum(TxnDirection))
    source: Mapped[TxnSource] = mapped_column(Enum(TxnSource), index=True)

    raw_description: Mapped[str] = mapped_column(Text)
    merchant_normalized: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    counterparty_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- Categorisation ----
    category: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tag_source: Mapped[TagSource | None] = mapped_column(Enum(TagSource), nullable=True)
    tag_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- People / IOUs ----
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    is_loan: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- Audit ----
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    person = relationship("Person", back_populates="transactions")

    __table_args__ = (UniqueConstraint("external_id", "source", name="uq_txn_external"),)

    @property
    def signed_amount(self) -> float:
        """Amount with debits negative. Convenient for summing net flow."""
        return -self.amount if self.direction == TxnDirection.DEBIT else self.amount


class Person(Base):
    """A counterparty the user moves money with repeatedly."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    relationship_type: Mapped[str] = mapped_column(String(32), default="friend")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    upi_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    transactions = relationship("Transaction", back_populates="person")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_essential: Mapped[bool] = mapped_column(Boolean, default=False)


class Rule(Base):
    """A regex tagging rule. Stored in the DB so it is editable without a deploy."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    pattern: Mapped[str] = mapped_column(String(256))
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[TxnDirection | None] = mapped_column(Enum(TxnDirection), nullable=True)
    source: Mapped[TxnSource | None] = mapped_column(Enum(TxnSource), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    person_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower wins
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MerchantNote(Base):
    """User-supplied context for a payee, shown in the report's "why notable" column.

    A table rather than a lookup in code: an annotation like "rent" or "my
    landlord" is a fact about one person's life, and those belong in that person's
    database, never in shipped source.
    """

    __tablename__ = "merchant_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Case-insensitive substring matched against the payee name.
    pattern: Mapped[str] = mapped_column(String(128), unique=True)
    note: Mapped[str] = mapped_column(String(160))
    priority: Mapped[int] = mapped_column(Integer, default=100)


class PipelineRun(Base):
    """Audit record for one pipeline execution."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|ok|error
    trigger: Mapped[str] = mapped_column(String(32), default="demo")   # demo|files|upload

    transactions_processed: Mapped[int] = mapped_column(Integer, default=0)
    rule_tagged: Mapped[int] = mapped_column(Integer, default=0)
    llm_tagged: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, default=0)

    # Set when the model was reachable for the whole run. False means some rows
    # were left untagged on purpose — the run is honest about it rather than
    # writing zero-confidence placeholder tags.
    llm_available: Mapped[bool] = mapped_column(Boolean, default=True)
    llm_failed: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_timings_ms: Mapped[dict] = mapped_column(JSON, default=dict)
