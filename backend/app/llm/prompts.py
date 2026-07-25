"""Prompts and the JSON schemas that constrain the model's replies.

The category taxonomy is defined once, in `CATEGORIES`, and reused three ways:
as the human-readable list inside the system prompt, as the `enum` in the JSON
schema that constrains decoding, and by `app.seed` when creating Category rows.
Keeping one source of truth is what stops the model from inventing
"Food & Dining" when the database only knows about "food".
"""
from __future__ import annotations

from typing import Any

# (name, description shown to the model)
CATEGORIES: list[tuple[str, str]] = [
    ("food", "eating out, food delivery, coffee, office cafeteria"),
    ("groceries", "supermarkets, quick-commerce, vegetables, daily essentials"),
    ("transport", "cabs, fuel, metro, buses, tolls, parking"),
    ("rent", "monthly housing rent only"),
    ("utilities", "electricity, water, gas, broadband, mobile recharge"),
    ("entertainment", "movies, events, gaming, ticketing"),
    ("shopping", "clothes, electronics, household goods, marketplaces"),
    ("health", "pharmacy, doctor, hospital, diagnostics, insurance premiums"),
    ("subscriptions", "recurring services — streaming, music, gym, cloud storage"),
    ("salary", "income from an employer"),
    ("investments", "mutual funds, stocks, SIPs, fixed deposits"),
    ("loan_given", "money you lent to someone"),
    ("loan_taken", "money you borrowed from someone"),
    ("loan_repayment", "paying back a loan or a credit-card bill, either direction"),
    ("cash", "ATM withdrawals and cash deposits"),
    ("uncategorized", "use when genuinely unsure — this is not a failure"),
]

CATEGORY_NAMES: list[str] = [name for name, _ in CATEGORIES]

_TAXONOMY_BLOCK = "\n".join(f"- {name}: {desc}" for name, desc in CATEGORIES)


# ---------------------------------------------------------------------------
# Transaction tagging
# ---------------------------------------------------------------------------

# `maxLength` on the free-text fields matters. Constrained decoding will happily
# let the model run a sentence on forever inside a string, and an over-long
# `reason` is the one place where output still degenerates. Capping it keeps
# replies short and the JSON valid.
TAGGING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORY_NAMES},
        "subcategory": {"type": ["string", "null"], "maxLength": 32},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 140},
    },
    "required": ["category", "confidence", "reason"],
}

TAGGING_SYSTEM = f"""You classify transactions from Indian bank accounts, UPI \
apps and credit cards. Assign exactly one category.

Categories:
{_TAXONOMY_BLOCK}

Rules:
- Prefer "uncategorized" over a guess. A payment to an individual whose name \
you do not recognise is uncategorized, not "food".
- `confidence` must reflect real certainty. Use 0.9+ only when the merchant is \
unmistakable, 0.3-0.6 when the payee is an unrecognised person or an opaque \
merchant string, and below 0.3 when the description carries no signal at all.
- A credit (money in) is never a spending category. Salary, refunds and \
repayments received are `salary`, `loan_repayment` or `uncategorized`.
- `reason` must be at most 12 words."""

TAGGING_USER = """description: {description}
merchant: {merchant}
amount: Rs {amount}
direction: {direction}
source: {source}
counterparty: {counterparty}"""


# ---------------------------------------------------------------------------
# Validator agent — second opinion on low-confidence tags
# ---------------------------------------------------------------------------

VALIDATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agree": {"type": "boolean"},
        "category": {"type": "string", "enum": CATEGORY_NAMES},
        "subcategory": {"type": ["string", "null"], "maxLength": 32},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 140},
    },
    "required": ["agree", "category", "confidence", "reason"],
}

VALIDATOR_SYSTEM = f"""You audit the output of a transaction classifier. You see \
one transaction and the classifier's call, and you either confirm it or replace it.

Categories:
{_TAXONOMY_BLOCK}

Rules:
- Only disagree when you are confident the classifier is wrong. "I would have \
phrased it differently" is agreement.
- If the description is genuinely ambiguous, agree with `uncategorized` and \
keep confidence low. Forcing a category on an opaque payee is the failure mode \
you exist to prevent.
- `reason` must be at most 12 words."""

VALIDATOR_USER = """Transaction:
  description: {description}
  merchant: {merchant}
  amount: Rs {amount}
  direction: {direction}
  source: {source}

Classifier said:
  category: {category}
  subcategory: {subcategory}
  confidence: {confidence}
  reason: {reason}"""


# ---------------------------------------------------------------------------
# Relationship classifier — what kind of money flow is this person?
# ---------------------------------------------------------------------------

RELATIONSHIP_KINDS = ["user_lends", "user_borrows", "split_bills", "settled", "vendor"]

RELATIONSHIP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": RELATIONSHIP_KINDS},
        "summary": {"type": "string", "maxLength": 200},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["kind", "summary", "confidence"],
}

RELATIONSHIP_SYSTEM = """You classify the money relationship between the account \
holder and one other person, from their transaction history.

Direction is written from the account holder's point of view:
  debit  = the account holder sent money to this person
  credit = the account holder received money from this person

Pick one kind:
  user_lends   - the account holder sends first and is paid back over time
  user_borrows - the account holder receives first and pays back over time
  split_bills  - sustained small two-way flow, roughly balanced
  settled      - lifetime sent and received are approximately equal
  vendor       - this is a business, not a friend (rent, salary paid out, a shop)

`summary` is one plain sentence, at most 25 words. Do not repeat yourself."""

RELATIONSHIP_USER = """Person: {name}
Sent to them: Rs {sent} across {sent_count} transactions
Received from them: Rs {received} across {received_count} transactions
Net: Rs {net}

History (oldest first, up to 20 rows):
{history}"""


# ---------------------------------------------------------------------------
# Insight cards
# ---------------------------------------------------------------------------

INSIGHTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 60},
                    "body": {"type": "string", "maxLength": 320},
                    "severity": {"type": "string", "enum": ["good", "info", "warn", "critical"]},
                    "metric": {"type": ["string", "null"], "maxLength": 40},
                },
                "required": ["title", "body", "severity"],
            },
        }
    },
    "required": ["insights"],
}

INSIGHTS_SYSTEM = """You are a direct, numerate personal-finance analyst writing \
3 to 5 insight cards about someone's spending.

Every card must:
- cite a real rupee figure from the data
- say something the numbers actually support
- suggest a concrete change when there is one worth making

Do not moralise, do not congratulate, and do not pad. `body` is 2-3 sentences.
If a large share of spending is uncategorized, say so plainly — an unreadable \
ledger is the most useful thing to flag."""

INSIGHTS_USER = """Window: last {months} months
Spent: Rs {spend:,.0f}
Received: Rs {total_credit:,.0f}
Net: Rs {net:,.0f}
Transactions: {txn_count}

Note: Rs {internal_transfers:,.0f} of credit-card bill payments is already
excluded from "Spent". Those move money between the user's own accounts and
appear on both statements, so counting them would double-count card spending.
Do not add them back, and do not describe them as spending.

Top categories by spend:
{categories}

Top merchants:
{merchants}

People (positive means they owe the account holder):
{people}"""


# ---------------------------------------------------------------------------
# Report narrative — plain text, never JSON
# ---------------------------------------------------------------------------
# Long prose inside a JSON string field is where constrained decoding breaks
# down: the model runs past the closing quote and the whole object is lost. So
# narrative calls ask for plain text and the caller does its own sanity check.

NARRATIVE_SOURCE_SYSTEM = """You are a financial analyst writing one section of a \
personal spending report. The reader is the account holder; address them as "you".

Write 2 or 3 paragraphs, 40-90 words each. Real prose — no bullet points, no \
headings, no preamble. Use concrete rupee figures taken from the data given to \
you, and never invent a number that is not there. If a large share of spending \
is uncategorized, say so rather than glossing over it. End every sentence with \
a period."""

NARRATIVE_SOURCE_USER = """Account: {label}
Transactions: {count}
Money out: Rs {debit:,.0f}
Money in: Rs {credit:,.0f}

Top categories:
{categories}

Top payees:
{merchants}

EMI plans on this account:
{emis}

Recurring subscriptions on this account:
{subscriptions}"""

NARRATIVE_HEADLINE_SYSTEM = """You write the opening paragraph of a personal \
spending report. Two or three sentences, second person, concrete rupee figures \
from the data. No bullet points, no heading, no preamble — just the paragraph."""

BEHAVIOUR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "thesis": {"type": "string", "maxLength": 90},
                    "evidence": {"type": "string", "maxLength": 420},
                },
                "required": ["thesis", "evidence"],
            },
        }
    },
    "required": ["observations"],
}

BEHAVIOUR_SYSTEM = """You write the closing section of a personal spending \
report, titled "What your spending says about you".

Produce 3 to 5 observations. Each has a `thesis` (one short sentence, the \
pattern you noticed) and `evidence` (2-3 sentences citing real rupee figures \
and payee names from the data).

Find patterns the account holder would not get from a pie chart — money that \
routes through them to settle group expenses, fixed obligations that quietly \
add up, a category that is invisible because it is untagged. Describe, do not \
scold. Each observation must make a different point."""


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

CHAT_SYSTEM = """You answer questions about the account holder's own spending, \
using only the summary provided below. Be specific and quote rupee figures.

If the summary does not contain what was asked, say so — do not estimate. Keep \
answers under six sentences unless asked for more detail."""


# ---------------------------------------------------------------------------
# Receipt scanning
# ---------------------------------------------------------------------------

RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean"},
        "merchant": {"type": ["string", "null"], "maxLength": 80},
        "amount": {"type": ["number", "null"]},
        "date": {"type": ["string", "null"], "maxLength": 10},
        "category": {"type": "string", "enum": CATEGORY_NAMES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "items": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 60}},
    },
    "required": ["is_receipt", "category", "confidence"],
}

RECEIPT_SYSTEM = f"""Read this receipt image and extract the merchant name, the \
grand total, and the date in YYYY-MM-DD form.

Categories:
{_TAXONOMY_BLOCK}

Set `is_receipt` to false if the image is not a receipt or bill, and leave the \
other fields null. Extract the total the customer paid, not a subtotal or an \
individual line item. If the date is not printed, leave it null rather than \
guessing."""
