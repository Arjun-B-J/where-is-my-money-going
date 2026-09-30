"""The agent's tools: read-only, exact queries over the transaction table.

These are what DECISIONS.md §15 chose over vector search for chat. A question
about money is a filter and an aggregate, and both have exact answers, so the
model is left with one job, choosing the filter. Every figure it can quote is
computed here.

Four rules shape the module.

**Totals cover every matching row.** `grand_total`, `count`, `total_matches`
and `total_amount` are computed before `top_n` or `limit` trims the listing. A
model shown ten groups and asked for the total would otherwise add up the ten
and quietly drop the rest.

**Spending means what the dashboard means.** Debit totals leave out card-bill
payments between the user's own accounts, using `is_internal_transfer` exactly
as `services.analytics` does, and duplicates are excluded the same way. Chat and
the dashboard must never disagree about a figure they both show.

**Bad arguments are answers, not exceptions.** Each tool validates its
arguments with a pydantic model, the same model that produces the JSON schema
the model is shown. Anything wrong (an unknown category, a malformed date, an
argument the tool does not take) goes back to the model as `{"error": ...}` so
it can try again. An unknown argument is rejected rather than ignored: dropping
`merchant="..."` silently would return the unfiltered total as though it were
the filtered one.

**Names can be reduced to initials.** `redact=True` is for callers that may be a
cloud model, such as the planned MCP server. A statement is full of other
people's names; see docs/PRIVACY.md.
"""
from __future__ import annotations

import calendar
import json
import logging
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.ingest.normalize import extract_vpa, split_upi_narration
from app.llm.prompts import CATEGORY_NAMES
from app.models import Category, Person, Transaction, TxnDirection
from app.reports.labels import initials, known_person_names, redact_payee, source_label
from app.services.cross_source import is_internal_transfer
from app.services.emi_detector import summarize_emi_plans
from app.services.subscriptions import detect_subscriptions

logger = logging.getLogger(__name__)

_MONTH = re.compile(r"^\d{4}-\d{2}$")


class ToolArgumentError(ValueError):
    """An argument that passed validation but names something that is not there."""


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


class _Args(BaseModel):
    # Unknown arguments are an error, not noise: see the module docstring.
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_empty(cls, data: Any) -> Any:
        """Read null and "" as "not given", so the argument's default applies.

        Models routinely send `"category": null` or `"text": ""` for a filter they
        mean to leave off. Rejecting that costs a round trip for nothing.
        """
        if not isinstance(data, dict):
            return data
        return {
            key: value for key, value in data.items()
            if value is not None and not (isinstance(value, str) and not value.strip())
        }


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _category_key(value: Any) -> Any:
    """`"Loan given"` and `"loan-given"` both mean the category `loan_given`."""
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s-]+", "_", value.strip().lower())


class NoArgs(_Args):
    """For a tool that takes no arguments."""


class _Period(_Args):
    start_date: date | None = Field(
        None,
        description="First day to include, YYYY-MM-DD. A YYYY-MM month means its first day.",
    )
    end_date: date | None = Field(
        None,
        description="Last day to include, inclusive, YYYY-MM-DD. A YYYY-MM month means its last day.",
    )

    @field_validator("start_date", mode="before")
    @classmethod
    def _month_start(cls, value: Any) -> Any:
        if isinstance(value, str) and _MONTH.match(value.strip()):
            return f"{value.strip()}-01"
        return value

    @field_validator("end_date", mode="before")
    @classmethod
    def _month_end(cls, value: Any) -> Any:
        # "2026-03" as an end date means up to and including 31 March, which is
        # what anyone asking about March wants. An impossible month is left for
        # the date parser to reject with its own message.
        if isinstance(value, str) and _MONTH.match(value.strip()):
            year, month = (int(part) for part in value.strip().split("-"))
            if 1 <= month <= 12:
                return date(year, month, calendar.monthrange(year, month)[1])
        return value

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date is after end_date")
        return self


class SpendingSummaryArgs(_Period):
    group_by: Literal["category", "payee", "month"] = Field(
        description="What to total by: category, payee or month (YYYY-MM).",
    )
    category: str | None = Field(
        None, description="Only this category, for example food. data_overview lists them."
    )
    direction: Literal["debit", "credit"] = Field(
        "debit", description="debit is money out (the default); credit is money in."
    )
    top_n: int = Field(
        10, ge=1, le=25,
        description="How many groups to list, 1 to 25 (default 10). grand_total and count "
                    "still cover every group.",
    )

    _normalise = field_validator("group_by", "direction", mode="before")(_lower)
    _normalise_category = field_validator("category", mode="before")(_category_key)


class FindTransactionsArgs(_Period):
    text: str | None = Field(
        None, description="Text to look for in the payee or the bank's description, any case."
    )
    category: str | None = Field(
        None, description="Only this category, for example food. data_overview lists them."
    )
    min_amount: float | None = Field(None, ge=0, description="Smallest amount to include, in rupees.")
    max_amount: float | None = Field(None, ge=0, description="Largest amount to include, in rupees.")
    direction: Literal["debit", "credit"] | None = Field(
        None, description="debit (money out) or credit (money in). Leave out for both."
    )
    sort: Literal["largest", "newest"] = Field(
        "largest", description="Which rows to list first: largest (the default) or newest."
    )
    limit: int = Field(
        10, ge=1, le=25,
        description="How many rows to list, 1 to 25 (default 10). total_matches and the totals "
                    "still cover every match.",
    )

    _normalise = field_validator("direction", "sort", mode="before")(_lower)
    _normalise_category = field_validator("category", mode="before")(_category_key)

    @model_validator(mode="after")
    def _amounts_in_order(self) -> Self:
        if (
            self.min_amount is not None and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("min_amount is greater than max_amount")
        return self


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _View:
    """How names appear in a result: in full, or reduced to initials."""

    redact: bool
    people: frozenset[str]

    @classmethod
    def build(cls, db: Session, redact: bool) -> _View:
        return cls(redact=redact, people=frozenset(known_person_names(db)) if redact else frozenset())

    def payee(self, payee: str) -> str:
        if not self.redact:
            return payee
        name = payee
        if extract_vpa(payee):
            # A raw UPI narration carries the payee's name in its second field
            # and usually again in the handle, so neither is passed through.
            candidate, _handle = split_upi_narration(payee)
            name = (candidate or "").strip()
            if not name or "@" in name:
                return "UPI payee"
        # Compared in capitals because the name-shape heuristic expects the way
        # banks print names; a detected subscription arrives title-cased.
        shouted = name.upper()
        if redact_payee(shouted, self.people) != shouted:
            return initials(name)
        # A detected person inside a longer narration ("NEFT-CR-SOME PERSON-REF")
        # fails the shape test, but the list of detected names still finds them.
        # Longest first, so a full name is replaced before an alias inside it.
        for person in sorted((p.strip() for p in self.people), key=len, reverse=True):
            if person and person.upper() in name.upper():
                name = re.sub(re.escape(person), initials(person), name, flags=re.IGNORECASE)
        return name

    def person(self, name: str) -> str:
        return redact_payee(name, self.people) if self.redact else name


def _money(amount: float) -> float:
    """Two decimals, and never a negative zero in the JSON."""
    rounded = round(amount, 2)
    return 0.0 if rounded == 0 else rounded


def _total(amounts: Iterable[float]) -> float:
    # fsum rather than sum: the same rows must give the same figure whatever
    # order the database returned them in.
    return _money(math.fsum(amounts))


def _category(txn: Transaction) -> str:
    return txn.category or "uncategorized"


def _payee(txn: Transaction) -> str:
    # The same key the dashboard's top-merchants table uses.
    return (txn.merchant_normalized or txn.raw_description or "Unknown").strip()


def _month(txn: Transaction) -> str:
    return txn.posted_at.strftime("%Y-%m")


def _filters(**values: Any) -> dict[str, Any]:
    """The filters a result was computed with, echoed so the model can check them."""
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items() if value is not None
    }


def _rows(
    db: Session,
    *,
    direction: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[Transaction]:
    """Non-duplicate rows in a date range, both ends inclusive."""
    query = db.query(Transaction).filter(Transaction.is_duplicate.is_(False))
    if direction is not None:
        query = query.filter(Transaction.direction == TxnDirection(direction))
    if start is not None:
        query = query.filter(Transaction.posted_at >= datetime.combine(start, time.min))
    if end is not None:
        query = query.filter(
            Transaction.posted_at < datetime.combine(end + timedelta(days=1), time.min)
        )
    return query.all()


def _category_names(db: Session) -> list[str]:
    names = [row.name for row in db.query(Category).order_by(Category.id).all()]
    return names or list(CATEGORY_NAMES)


def _check_category(db: Session, category: str | None) -> str | None:
    """Reject a category the data does not have.

    Without this, a filter on "dining" matches nothing and the model reports
    that the user spent ₹0 on dining, which is an answer to a different
    question.
    """
    if category is None:
        return None
    in_use = {row[0] for row in db.query(Transaction.category).distinct() if row[0]}
    known = set(_category_names(db)) | set(CATEGORY_NAMES) | in_use
    if category not in known:
        raise ToolArgumentError(
            f"unknown category {category!r}; use one of: {', '.join(sorted(known))}"
        )
    return category


def _coverage(db: Session, start: date | None, end: date | None) -> dict[str, Any] | None:
    """Say so when the period asked about falls outside the loaded data.

    Zero and "no data" are different answers. A month before the first statement
    has no rows, and a total of 0 for it reads as "you spent nothing", which is
    false. On the chat eval a model said exactly that, although the system prompt
    stated the data range; the fact has to sit in the result it is reading. The
    same distinction as a NULL tag versus a zero-confidence one (DECISIONS.md §4).
    """
    if start is None and end is None:
        return None
    first, last, count = data_range(db)
    if not count or first is None or last is None:
        return {"note": "no transactions have been loaded, so nothing was recorded for any period"}
    low, high = start or first, end or last
    span = {"data_from": first.isoformat(), "data_to": last.isoformat()}
    if high < first or low > last:
        return {**span, "note": "this period is outside the data: nothing was recorded for it, "
                                "which is not the same as zero"}
    if low < first or high > last:
        return {**span, "note": "part of this period is outside the data: the totals cover only "
                                "the part that was recorded"}
    return None


def data_range(db: Session) -> tuple[date | None, date | None, int]:
    """First and last transaction dates and the number of transactions."""
    first, last, count = (
        db.query(
            func.min(Transaction.posted_at),
            func.max(Transaction.posted_at),
            func.count(Transaction.id),
        )
        .filter(Transaction.is_duplicate.is_(False))
        .one()
    )
    return (first.date() if first else None, last.date() if last else None, int(count or 0))


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def _data_overview(db: Session, view: _View, _args: NoArgs) -> dict[str, Any]:
    first, last, count = data_range(db)
    uncategorized = (
        db.query(func.count(Transaction.id))
        .filter(
            Transaction.is_duplicate.is_(False),
            or_(Transaction.category.is_(None), Transaction.category == "uncategorized"),
        )
        .scalar()
    )
    return {
        "first_date": first.isoformat() if first else None,
        "last_date": last.isoformat() if last else None,
        "transaction_count": count,
        "uncategorized_count": int(uncategorized or 0),
        "categories": _category_names(db),
    }


def _spending_summary(db: Session, view: _View, args: SpendingSummaryArgs) -> dict[str, Any]:
    category = _check_category(db, args.category)
    rows = _rows(db, direction=args.direction, start=args.start_date, end=args.end_date)
    if category is not None:
        rows = [txn for txn in rows if _category(txn) == category]

    # is_internal_transfer is only ever true for bank-side debits, so for money
    # in this removes nothing.
    internal = [txn for txn in rows if is_internal_transfer(txn)]
    counted = [txn for txn in rows if not is_internal_transfer(txn)]

    key: Callable[[Transaction], str] = {
        "category": _category, "payee": _payee, "month": _month,
    }[args.group_by]
    amounts: dict[str, list[float]] = defaultdict(list)
    for txn in counted:
        amounts[key(txn)].append(txn.amount)
    totals = {group: _total(values) for group, values in amounts.items()}
    ranked = sorted(totals, key=lambda group: (-totals[group], group))
    listed = ranked[:args.top_n]

    label = view.payee if args.group_by == "payee" else str
    result: dict[str, Any] = {
        "filters": _filters(
            group_by=args.group_by, direction=args.direction, category=category,
            start_date=args.start_date, end_date=args.end_date,
        ),
        "grand_total": _total(txn.amount for txn in counted),
        "count": len(counted),
        "groups": [
            {args.group_by: label(group), "total": totals[group], "count": len(amounts[group])}
            for group in listed
        ],
        "groups_listed": len(listed),
        "groups_in_total": len(ranked),
    }
    if len(listed) < len(ranked):
        result["not_listed_total"] = _total(
            amount for group in ranked[args.top_n:] for amount in amounts[group]
        )
    if internal:
        result["card_bill_payments_left_out"] = {
            "count": len(internal), "total": _total(txn.amount for txn in internal),
        }
    if coverage := _coverage(db, args.start_date, args.end_date):
        result["coverage"] = coverage
    return result


def _find_transactions(db: Session, view: _View, args: FindTransactionsArgs) -> dict[str, Any]:
    category = _check_category(db, args.category)
    needle = args.text.lower() if args.text else None

    def wanted(txn: Transaction) -> bool:
        if category is not None and _category(txn) != category:
            return False
        if args.min_amount is not None and txn.amount < args.min_amount:
            return False
        if args.max_amount is not None and txn.amount > args.max_amount:
            return False
        if needle is None:
            return True
        return any(
            needle in (field or "").lower()
            for field in (txn.raw_description, txn.merchant_normalized, txn.counterparty_id)
        )

    matches = [
        txn for txn in _rows(db, direction=args.direction, start=args.start_date, end=args.end_date)
        if wanted(txn)
    ]
    if args.sort == "largest":
        matches.sort(key=lambda txn: (txn.amount, txn.posted_at), reverse=True)
    else:
        matches.sort(key=lambda txn: (txn.posted_at, txn.amount), reverse=True)
    listed = matches[:args.limit]

    def row(txn: Transaction) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "date": txn.posted_at.date().isoformat(),
            "payee": view.payee(_payee(txn)),
            "amount": _money(txn.amount),
            "direction": txn.direction.value,
            "category": _category(txn),
        }
        if is_internal_transfer(txn):
            entry["card_bill_payment"] = True
        return entry

    result: dict[str, Any] = {
        "filters": _filters(
            text=args.text, category=category, direction=args.direction,
            min_amount=args.min_amount, max_amount=args.max_amount,
            start_date=args.start_date, end_date=args.end_date, sort=args.sort,
        ),
        "total_matches": len(matches),
    }
    if args.direction is not None:
        result["total_amount"] = _total(txn.amount for txn in matches)
    else:
        # With both directions in play there is no single total worth giving: a
        # sum of money sent and money received is not a figure anyone means, and
        # a model asked "how much did I pay X" would quote it.
        for direction in TxnDirection:
            side = [txn for txn in matches if txn.direction == direction]
            result[direction.value] = {"count": len(side), "total": _total(t.amount for t in side)}
    internal = [txn for txn in matches if is_internal_transfer(txn)]
    if internal:
        result["card_bill_payments"] = {
            "count": len(internal),
            "total": _total(txn.amount for txn in internal),
            "note": "payments between the user's own accounts, not spending",
        }
    result["rows"] = [row(txn) for txn in listed]
    result["rows_listed"] = len(listed)
    if coverage := _coverage(db, args.start_date, args.end_date):
        result["coverage"] = coverage
    return result


def _people_balances(db: Session, view: _View, _args: NoArgs) -> dict[str, Any]:
    people: list[dict[str, Any]] = []
    for person in db.query(Person).order_by(Person.id).all():
        rows = (
            db.query(Transaction)
            .filter(Transaction.person_id == person.id, Transaction.is_duplicate.is_(False))
            .all()
        )
        if not rows:
            continue
        # The dashboard's rule: with a friend the whole two-way history is the
        # running balance; with anyone else only rows marked as loans count,
        # because paying a landlord is not an IOU.
        ledger = rows if person.relationship_type == "friend" else [t for t in rows if t.is_loan]
        sent = math.fsum(t.amount for t in ledger if t.direction == TxnDirection.DEBIT)
        received = math.fsum(t.amount for t in ledger if t.direction == TxnDirection.CREDIT)
        # Field names say who paid whom. They used to be `sent` and `received`,
        # and on the chat eval a model read `sent` as money the person sent the
        # user: the right figure for the wrong direction, a mistake the figure
        # check cannot catch because the number is real.
        people.append({
            "name": view.person(person.name),
            "relationship": person.relationship_type,
            "user_sent_to_them": _money(sent),
            "they_sent_to_user": _money(received),
            "net": _money(sent - received),
            "transactions": len(rows),
        })
    people.sort(key=lambda entry: abs(entry["net"]), reverse=True)
    return {
        "net_means": "positive: they owe the user; negative: the user owes them",
        "people": people,
    }


def _recurring_payments(db: Session, view: _View, _args: NoArgs) -> dict[str, Any]:
    subscriptions = detect_subscriptions(db)
    plans = summarize_emi_plans(db)
    return {
        "subscriptions": [
            {
                "service": view.payee(sub.service),
                "cadence": sub.cadence,
                "typical_amount": _money(sub.median_amount),
                "occurrences": sub.occurrences,
                "first_seen": sub.first_seen.date().isoformat(),
                "last_seen": sub.last_seen.date().isoformat(),
                "annual_estimate": _money(sub.annual_estimate),
                "account": source_label(sub.source),
            }
            for sub in subscriptions
        ],
        "subscriptions_annual_total": _total(sub.annual_estimate for sub in subscriptions),
        "instalment_plans": [
            {
                "merchant": view.payee(plan.merchant),
                "monthly_amount": _money(plan.monthly_amount),
                "instalments_paid": plan.installments_seen,
                "instalments_total": plan.installments_total,
                "progress": plan.progress_label,
                "completed": plan.completed,
                "total_paid": _money(plan.total_paid),
                "first_seen": plan.first_seen.date().isoformat(),
                "last_seen": plan.last_seen.date().isoformat(),
                "account": source_label(plan.source),
            }
            for plan in plans
        ],
    }


# ---------------------------------------------------------------------------
# Registry and dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    run: Callable[[Session, _View, Any], dict[str, Any]]


TOOLS: dict[str, Tool] = {
    tool.name: tool for tool in (
        Tool(
            "data_overview",
            "What data exists: the first and last transaction dates, how many transactions "
            "there are, how many are uncategorized, and the category names the other tools "
            "accept.",
            NoArgs, _data_overview,
        ),
        Tool(
            "spending_summary",
            "Exact totals and counts grouped by category, payee or month, optionally within "
            "a date range and one category. grand_total and count cover every matching row, "
            "not only the groups listed. Groups are sorted largest first. Money out leaves "
            "out credit-card bill payments between the user's own accounts, as the dashboard "
            "does.",
            SpendingSummaryArgs, _spending_summary,
        ),
        Tool(
            "find_transactions",
            "Search individual transactions by text, category, dates, amount range or "
            "direction. Returns the number of matches and their total (split into money out "
            "and money in when no direction is given), then the largest or newest rows.",
            FindTransactionsArgs, _find_transactions,
        ),
        Tool(
            "people_balances",
            "The running balance with each person the user moves money with: what the user "
            "sent to them, what they sent to the user, and the net. Positive net means they "
            "owe the user; negative means the user owes them.",
            NoArgs, _people_balances,
        ),
        Tool(
            "recurring_payments",
            "Subscriptions detected from regularly repeating charges, with cadence, typical "
            "amount and annual estimate, and instalment (EMI) plans with their progress and "
            "total paid.",
            NoArgs, _recurring_payments,
        ),
    )
}


def _parameters(model: type[BaseModel]) -> dict[str, Any]:
    """The pydantic schema, flattened into the shape Ollama reads.

    Ollama keeps only `type`, `description` and `enum` for each property and
    drops the rest, including `anyOf`. Pydantic writes an optional field as
    `anyOf: [<type>, null]`, which would reach the model with no type at all, so
    each property is reduced to its non-null type. Optionality is carried by
    `required`, and the bounds are in each description.
    """
    schema = model.model_json_schema()
    properties: dict[str, Any] = {}
    for name, spec in schema.get("properties", {}).items():
        options = spec.get("anyOf", [spec])
        concrete: dict[str, Any] = next(
            (option for option in options if option.get("type") != "null"), {}
        )
        entry: dict[str, Any] = {
            "type": concrete.get("type", "string"),
            "description": spec.get("description", ""),
        }
        if "enum" in concrete:
            entry["enum"] = concrete["enum"]
        properties[name] = entry
    return {"type": "object", "properties": properties, "required": list(schema.get("required", []))}


# The `tools` list sent with every call, in Ollama's function-calling shape.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": _parameters(tool.args),
        },
    }
    for tool in TOOLS.values()
]


def parse_arguments(raw: Any) -> dict[str, Any] | None:
    """A tool call's arguments as a dict, or None if they are not an object.

    Ollama sends a dict. Some models put a JSON string there instead, which is
    the same arguments in a different envelope and is accepted.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


def _describe(error: ValidationError) -> str:
    parts: list[str] = []
    for problem in error.errors():
        where = ".".join(str(part) for part in problem["loc"])
        message = str(problem["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


def execute(db: Session, name: str, args: Any, *, redact: bool = False) -> dict[str, Any]:
    """Run one tool and return its result, or `{"error": ...}` for the model.

    Never raises. A mistake in the call is information the model can act on,
    and a failure inside a tool is logged with its traceback and reported to
    the model as a failure, so it cannot mistake it for an empty result.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}; available tools: {', '.join(TOOLS)}"}

    parsed = parse_arguments(args)
    if parsed is None:
        return {"error": f"arguments for {name} must be a JSON object"}
    try:
        validated = tool.args.model_validate(parsed)
    except ValidationError as e:
        accepted = ", ".join(tool.args.model_fields) or "no arguments"
        return {"error": f"invalid arguments for {name}: {_describe(e)}. It accepts: {accepted}."}

    try:
        return tool.run(db, _View.build(db, redact), validated)
    except ToolArgumentError as e:
        return {"error": str(e)}
    except Exception:
        logger.exception("Tool %s failed", name)
        return {"error": f"{name} failed while reading the database; this is not an empty result"}
