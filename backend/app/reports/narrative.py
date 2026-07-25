"""Prose for the report, written by the local model.

Three passes: an opening paragraph, one section per account, and a closing set
of behavioural observations.

Prose is requested as **plain text**, never as a string inside a JSON object.
Constrained decoding cannot bound the length of a free-text field reliably, and
a paragraph that runs past its closing quote destroys the whole object. Only the
closing observations use a schema, because there the structure (a list of
thesis/evidence pairs) is worth more than the extra length headroom.

When the model is unavailable, these functions return a deterministic summary
with `generated_by="computed"`. The report labels those sections so a reader can
tell prose that was written from prose that was assembled — passing canned text
off as analysis is exactly the kind of quiet dishonesty this project is trying
to avoid.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.llm.client import LLMClient
from app.llm.prompts import (
    BEHAVIOUR_SCHEMA,
    BEHAVIOUR_SYSTEM,
    NARRATIVE_HEADLINE_SYSTEM,
    NARRATIVE_SOURCE_SYSTEM,
    NARRATIVE_SOURCE_USER,
)
from app.models import Transaction, TxnDirection
from app.money import rupees
from app.pipeline.validator import looks_degenerate
from app.reports.labels import source_label
from app.services.cross_source import is_internal_transfer
from app.services.emi_detector import summarize_emi_plans
from app.services.patterns import detect_friend_loops
from app.services.subscriptions import detect_subscriptions

logger = logging.getLogger(__name__)

Origin = Literal["model", "computed"]

# Sanity bounds for a generated section. Wide on purpose: the gate is here to
# catch broken generations, not to enforce a word count.
_MIN_CHARS = 80
_MAX_CHARS = 2_400


@dataclass
class SourceSummary:
    """Aggregates for one account, used both for prose and for charts."""

    source: str
    label: str
    txn_count: int
    debit_total: float
    credit_total: float
    top_categories: list[tuple[str, float]] = field(default_factory=list)
    top_payees: list[tuple[str, float]] = field(default_factory=list)

    @property
    def uncategorized_share(self) -> float:
        for name, value in self.top_categories:
            if name == "uncategorized":
                return value / self.debit_total if self.debit_total else 0.0
        return 0.0


@dataclass
class NarrativeBlock:
    heading: str
    body: str
    generated_by: Origin = "model"

    @property
    def is_computed(self) -> bool:
        return self.generated_by == "computed"


def build_source_summary(db: Session, source: str) -> SourceSummary:
    """Totals, top categories and top payees for one account."""
    txns = (
        db.query(Transaction)
        .filter(Transaction.is_duplicate.is_(False), Transaction.source == source)
        .all()
    )

    categories: dict[str, float] = defaultdict(float)
    payees: dict[str, float] = defaultdict(float)
    debit = credit = 0.0

    for txn in txns:
        if txn.direction == TxnDirection.CREDIT:
            credit += txn.amount
            continue
        debit += txn.amount
        categories[txn.category or "uncategorized"] += txn.amount
        payees[txn.merchant_normalized or txn.raw_description[:40]] += txn.amount

    return SourceSummary(
        source=source,
        label=source_label(source),
        txn_count=len(txns),
        debit_total=round(debit, 2),
        credit_total=round(credit, 2),
        top_categories=sorted(categories.items(), key=lambda kv: -kv[1])[:8],
        top_payees=sorted(payees.items(), key=lambda kv: -kv[1])[:10],
    )


def _acceptable(text: str) -> bool:
    """Reject empty, truncated or degenerate generations."""
    if not (_MIN_CHARS <= len(text) <= _MAX_CHARS):
        return False
    if looks_degenerate(text, max_words=600):
        return False
    # A section that never reaches a full stop was cut off mid-sentence.
    return text.count(".") >= 2


def _bullet_block(lines: list[str]) -> str:
    return "\n".join(f"  - {line}" for line in lines) or "  (none)"


# ---------------------------------------------------------------------------
# Per-account section
# ---------------------------------------------------------------------------


async def narrate_source(db: Session, source: str, llm: LLMClient) -> NarrativeBlock:
    """Write the analytical commentary for one account."""
    summary = build_source_summary(db, source)
    if summary.txn_count == 0:
        return NarrativeBlock(summary.label, "No activity on this account.", "computed")

    emis = [plan for plan in summarize_emi_plans(db) if plan.source == source]
    subs = [sub for sub in detect_subscriptions(db) if sub.source == source]

    prompt = NARRATIVE_SOURCE_USER.format(
        label=summary.label,
        count=summary.txn_count,
        debit=summary.debit_total,
        credit=summary.credit_total,
        categories=_bullet_block(
            [f"{name.replace('_', ' ')}: {rupees(value)}" for name, value in summary.top_categories]
        ),
        merchants=_bullet_block(
            [f"{name}: {rupees(value)}" for name, value in summary.top_payees]
        ),
        emis=_bullet_block([
            f"{plan.merchant}: {rupees(plan.monthly_amount)}/month, {plan.progress_label}"
            for plan in emis[:5]
        ]),
        subscriptions=_bullet_block([
            f"{sub.service}: {rupees(sub.median_amount)} {sub.cadence}"
            for sub in subs[:8]
        ]),
    )

    # Two attempts. Generation is cheap locally and the occasional bad sample is
    # better retried than papered over.
    for attempt in (1, 2):
        result = await llm.complete(
            [
                {"role": "system", "content": NARRATIVE_SOURCE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        if result.failed:
            logger.info("Narrative for %s unavailable: %s", source, result.error)
            break
        if _acceptable(result.text):
            return NarrativeBlock(summary.label, result.text, "model")
        logger.info("Narrative for %s rejected on attempt %d", source, attempt)

    return NarrativeBlock(summary.label, _computed_source_prose(summary, emis, subs), "computed")


def _computed_source_prose(summary: SourceSummary, emis: list, subs: list) -> str:
    """Assemble a factual summary from the aggregates. No interpretation."""
    paragraphs: list[str] = []

    opening = (
        f"Across {summary.txn_count} transactions on this account, "
        f"{rupees(summary.debit_total)} went out and "
        f"{rupees(summary.credit_total)} came in."
    )
    if summary.top_categories:
        name, value = summary.top_categories[0]
        share: float = value / summary.debit_total * 100 if summary.debit_total else 0.0
        opening += (
            f" The largest category is {name} at {rupees(value)}, "
            f"{share:.0f}% of everything spent here."
        )
    paragraphs.append(opening)

    if summary.top_payees:
        top = "; ".join(f"{name} {rupees(value)}" for name, value in summary.top_payees[:3])
        line = f"The biggest payees are {top}."
        remainder = sum(value for _, value in summary.top_payees[3:8])
        if remainder:
            line += f" The next five add {rupees(remainder)}."
        paragraphs.append(line)

    commitments: list[str] = []
    if emis:
        commitments.append(
            "Active instalment plans: "
            + ", ".join(
                f"{plan.merchant} at {rupees(plan.monthly_amount)}/month ({plan.progress_label})"
                for plan in emis[:3]
            )
            + "."
        )
    if subs:
        annual = sum(sub.annual_estimate for sub in subs)
        commitments.append(
            "Recurring charges: "
            + ", ".join(f"{sub.service} ({rupees(sub.median_amount)} {sub.cadence})"
                        for sub in subs[:4])
            + f" — about {rupees(annual)} a year."
        )
    if commitments:
        paragraphs.append(" ".join(commitments))

    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Opening paragraph
# ---------------------------------------------------------------------------


async def headline(db: Session, llm: LLMClient) -> NarrativeBlock:
    """Write the report's opening paragraph.

    Given the reconciled spend total, not the sum of every debit. Per-account
    figures stay gross — a card-bill payment really did leave the bank account —
    but the cross-account total has to exclude it, or this paragraph contradicts
    the headline number printed directly above it.
    """
    sources = sorted({txn.source.value for txn in db.query(Transaction).all()})
    if not sources:
        return NarrativeBlock("Headline", "No transactions in this period.", "computed")

    summaries = [build_source_summary(db, source) for source in sources]
    internal = sum(
        txn.amount
        for txn in db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()
        if is_internal_transfer(txn)
    )
    total_in = sum(s.credit_total for s in summaries)
    spend = sum(s.debit_total for s in summaries) - internal

    data = "\n".join([
        f"TOTAL SPENT (use this figure): {rupees(spend)}",
        f"TOTAL RECEIVED: {rupees(total_in)}",
        f"NET: {rupees(total_in - spend)}",
        "",
        f"Excluded from total spent: {rupees(internal)} of credit-card bill "
        "payments. Those move between the account holder's own accounts and appear "
        "on both statements, so counting them would double-count card spending. Do "
        "not mention them as spending and do not add them back.",
        "",
        "PER ACCOUNT (gross, and these do include the transfers above):",
        *[
            f"  {s.label}: {s.txn_count} transactions, "
            f"{rupees(s.debit_total)} out, {rupees(s.credit_total)} in"
            for s in summaries
        ],
    ])

    result = await llm.complete(
        [
            {"role": "system", "content": NARRATIVE_HEADLINE_SYSTEM},
            {"role": "user", "content": f"Data:\n{data}\n\nWrite the paragraph."},
        ],
        temperature=0.3,
    )
    if result.ok and _acceptable(result.text):
        return NarrativeBlock("Headline", result.text.strip('"'), "model")

    net = total_in - spend
    return NarrativeBlock(
        "Headline",
        f"Across {len(summaries)} account(s), {rupees(total_in)} came in and "
        f"{rupees(spend)} was spent, a net "
        f"{'surplus' if net >= 0 else 'shortfall'} of {rupees(abs(net))}.",
        "computed",
    )


# ---------------------------------------------------------------------------
# Closing observations
# ---------------------------------------------------------------------------


async def behavioural_observations(
    db: Session, llm: LLMClient
) -> tuple[list[dict], Origin]:
    """Produce the closing "what your spending says about you" observations."""
    emis = summarize_emi_plans(db)
    subs = detect_subscriptions(db)
    loops = detect_friend_loops(db)
    sources = sorted({txn.source.value for txn in db.query(Transaction).all()})
    summaries = [build_source_summary(db, source) for source in sources]

    payees: dict[str, float] = defaultdict(float)
    for summary in summaries:
        for name, value in summary.top_payees:
            payees[name] += value
    ranked_payees = sorted(payees.items(), key=lambda kv: -kv[1])[:15]

    def block(title: str, lines: list[str]) -> str:
        return f"{title}\n" + ("\n".join(lines) if lines else "  (none detected)")

    prompt = "\n\n".join([
        block("ACCOUNT TOTALS:", [
            f"  {s.label}: {s.txn_count} transactions, {rupees(s.debit_total)} out, "
            f"{rupees(s.credit_total)} in"
            for s in summaries
        ]),
        block("TOP PAYEES ACROSS ALL ACCOUNTS:", [
            f"  {name}: {rupees(value)}" for name, value in ranked_payees
        ]),
        block("INSTALMENT PLANS:", [
            f"  {p.merchant}: {rupees(p.monthly_amount)}/month, {p.progress_label}, "
            f"{rupees(p.total_paid)} paid so far"
            for p in emis[:8]
        ]),
        block("RECURRING CHARGES:", [
            f"  {s.service}: {rupees(s.median_amount)} {s.cadence}, "
            f"about {rupees(s.annual_estimate)} a year"
            for s in subs[:10]
        ]),
        block("TWO-WAY FLOWS WITH PEOPLE:", [
            f"  {loop.counterparty}: sent {rupees(loop.sent)} ({loop.sent_count}x), "
            f"received {rupees(loop.received)} ({loop.received_count}x), "
            f"net {rupees(abs(loop.net))} "
            f"{'owed to you' if loop.net > 0 else 'owed by you'}"
            for loop in loops[:6]
        ]),
    ])

    result = await llm.structured(
        [
            {"role": "system", "content": BEHAVIOUR_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        schema=BEHAVIOUR_SCHEMA,
        temperature=0.4,
    )
    parsed = result.json()
    if parsed:
        observations = [
            {"thesis": item["thesis"].strip(), "evidence": item["evidence"].strip()}
            for item in parsed.get("observations", [])
            if isinstance(item, dict) and item.get("thesis") and item.get("evidence")
            and not looks_degenerate(item["evidence"])
        ]
        if len(observations) >= 2:
            return observations[:5], "model"
        logger.info("Behavioural pass returned too few usable observations")

    return _computed_observations(summaries, emis, subs, loops), "computed"


def _computed_observations(
    summaries: list[SourceSummary], emis: list, subs: list, loops: list
) -> list[dict]:
    """Deterministic observations derived from the detectors."""
    observations: list[dict] = []

    total_debit = sum(s.debit_total for s in summaries)
    untagged = sum(
        value
        for s in summaries
        for name, value in s.top_categories
        if name == "uncategorized"
    )
    if total_debit and untagged / total_debit > 0.25:
        observations.append({
            "thesis": "A large share of your spending has no category yet.",
            "evidence": (
                f"{rupees(untagged)} of {rupees(total_debit)} "
                f"({untagged / total_debit * 100:.0f}%) is still uncategorized. "
                "Most of it is payments to individuals, which no rule can label "
                "without knowing who they are. Tagging the top few by hand "
                "resolves most of the total."
            ),
        })

    if subs:
        annual = sum(sub.annual_estimate for sub in subs)
        names = ", ".join(sub.service for sub in subs[:3])
        observations.append({
            "thesis": "Recurring charges add up to real money each year.",
            "evidence": (
                f"About {rupees(annual)} a year flows through {len(subs)} recurring "
                f"charges ({names}{' and others' if len(subs) > 3 else ''}). "
                "Each line item is small, which is why this total is easy to miss."
            ),
        })

    if loops:
        biggest = max(loops, key=lambda loop: loop.sent + loop.received)
        direction = "owed to you" if biggest.net > 0 else "owed by you"
        observations.append({
            "thesis": "Money routes through you on someone else's behalf.",
            "evidence": (
                f"With {biggest.counterparty} you sent {rupees(biggest.sent)} across "
                f"{biggest.sent_count} transactions and received "
                f"{rupees(biggest.received)} across {biggest.received_count}. "
                f"Net {rupees(abs(biggest.net))} {direction}. Gross flow like this "
                "inflates your spending totals well beyond what you actually consumed."
            ),
        })

    active = [plan for plan in emis if not plan.completed]
    if active:
        monthly = sum(plan.monthly_amount for plan in active)
        observations.append({
            "thesis": f"You are committed to {rupees(monthly)} a month in instalments.",
            "evidence": (
                f"{len(active)} active plan(s): "
                + ", ".join(f"{plan.merchant} ({plan.progress_label})" for plan in active[:3])
                + ". These continue regardless of what else changes in a given month."
            ),
        })

    fixed = sum(sub.annual_estimate / 12 for sub in subs) + sum(
        plan.monthly_amount for plan in active
    )
    if fixed:
        observations.append({
            "thesis": f"Your baseline burn is about {rupees(fixed)} a month.",
            "evidence": (
                f"Recurring charges and instalments come to roughly {rupees(fixed)} "
                "every month before rent, food or anything discretionary. That is the "
                "number worth knowing before a job change or a career break."
            ),
        })

    return observations[:5]
