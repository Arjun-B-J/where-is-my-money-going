"""Request and response shapes for the API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Whether a piece of text came from the local model or was assembled from the
# numbers. Surfaced so the UI can label it — presenting computed fallback text
# as model analysis is the kind of small dishonesty that erodes trust in the
# whole tool.
Origin = Literal["model", "computed"]


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    posted_at: datetime
    amount: float
    direction: str
    source: str
    raw_description: str
    merchant_normalized: str | None
    category: str | None
    subcategory: str | None
    # None means nothing has successfully categorised this row yet. It is never
    # set to "llm" for a call that failed.
    tag_source: str | None
    tag_confidence: float | None
    tag_reason: str | None
    person_id: int | None
    is_loan: bool
    needs_review: bool


class TransactionTagUpdate(BaseModel):
    category: str
    subcategory: str | None = None
    person_id: int | None = None


class BulkTagRequest(BaseModel):
    transaction_ids: list[int] = Field(min_length=1)
    category: str
    subcategory: str | None = None


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    relationship_type: str
    notes: str | None


class PersonBalance(BaseModel):
    person: PersonOut
    they_owe_you: float  # positive means they are behind with you
    transaction_count: int


class CategorySpend(BaseModel):
    category: str
    total: float
    count: int
    is_essential: bool


class MonthlySpend(BaseModel):
    month: str  # YYYY-MM
    debit_total: float
    credit_total: float
    net: float


class MerchantSpend(BaseModel):
    merchant: str
    total: float
    count: int
    last_seen: datetime


class DashboardSummary(BaseModel):
    # Gross money out, including transfers between the user's own accounts.
    total_debit: float
    # What was actually spent: total_debit minus those internal transfers. `net`
    # uses this, because netting the gross figure double-counts card spending.
    spend: float
    internal_transfers: float
    total_credit: float
    net: float
    transaction_count: int
    needs_review: int
    monthly: list[MonthlySpend]
    by_category: list[CategorySpend]
    top_merchants: list[MerchantSpend]
    people: list[PersonBalance]


class PipelineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    trigger: str
    transactions_processed: int
    rule_tagged: int
    llm_tagged: int
    needs_review: int
    # False means the model could not be reached for part of the run, so some
    # rows are deliberately untagged.
    llm_available: bool
    llm_failed: int
    error_message: str | None
    node_timings_ms: dict


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class InsightCard(BaseModel):
    title: str
    body: str
    severity: Literal["good", "info", "warn", "critical"] = "info"
    metric: str | None = None
    generated_by: Origin = "model"


class MerchantNoteIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=128)
    note: str = Field(min_length=2, max_length=160)
    priority: int = 100


class MerchantNoteOut(MerchantNoteIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class IngestFileResult(BaseModel):
    file: str
    parser: str
    parsed: int
    inserted: int
    duplicates: int
    rule_tagged: int | None = None
    llm_tagged: int | None = None
    needs_review: int | None = None
    llm_available: bool | None = None


class SupportedFormat(BaseModel):
    name: str
    label: str
    extensions: str
