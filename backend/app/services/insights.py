"""Insight cards for the dashboard.

The model gets an aggregate summary — never raw transaction rows — and returns
3 to 5 short observations. When it is unavailable, the computed cards below are
returned instead and flagged with `generated_by="computed"` so the UI can label
them rather than implying the model wrote them.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.llm.client import LLMClient, get_llm
from app.llm.prompts import INSIGHTS_SCHEMA, INSIGHTS_SYSTEM, INSIGHTS_USER
from app.money import rupees
from app.schemas import DashboardSummary, InsightCard
from app.services.analytics import dashboard_summary

logger = logging.getLogger(__name__)

# A quarter of spending with no category is the most useful thing to say about
# a ledger, so it outranks anything else the cards might mention.
_UNCATEGORIZED_ALERT_SHARE = 0.25


def _lines(rows: list[str]) -> str:
    return "\n".join(f"  - {row}" for row in rows) or "  (none)"


async def generate_insights(
    db: Session, *, months: int = 12, llm: LLMClient | None = None
) -> list[InsightCard]:
    """Return dashboard insight cards, model-written where possible."""
    llm = llm or get_llm()
    summary = dashboard_summary(db, months=months)

    if not summary.transaction_count:
        return [InsightCard(
            title="No data yet",
            body="Add statements on the Ingest page, or load the demo dataset, "
                 "to see where your money goes.",
            severity="info",
            generated_by="computed",
        )]

    prompt = INSIGHTS_USER.format(
        months=months,
        spend=summary.spend,
        internal_transfers=summary.internal_transfers,
        total_credit=summary.total_credit,
        net=summary.net,
        txn_count=summary.transaction_count,
        categories=_lines([
            f"{row.category}: {rupees(row.total)} over {row.count} transactions"
            for row in summary.by_category[:8]
        ]),
        merchants=_lines([
            f"{row.merchant}: {rupees(row.total)} over {row.count} transactions"
            for row in summary.top_merchants[:8]
        ]),
        people=_lines([
            f"{row.person.name}: {rupees(row.they_owe_you)} over "
            f"{row.transaction_count} transactions"
            for row in summary.people[:6]
        ]),
    )

    result = await llm.structured(
        [
            {"role": "system", "content": INSIGHTS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        schema=INSIGHTS_SCHEMA,
        temperature=0.4,
    )
    parsed = result.json()
    if parsed:
        cards = [
            InsightCard(
                title=item["title"],
                body=item["body"],
                severity=item.get("severity", "info"),
                metric=item.get("metric"),
                generated_by="model",
            )
            for item in parsed.get("insights", [])
            if isinstance(item, dict) and item.get("title") and item.get("body")
        ]
        if cards:
            return cards
        logger.info("Model returned no usable insight cards")
    else:
        logger.info("Insights unavailable from the model: %s", result.error)

    return computed_insights(summary)


def computed_insights(summary: DashboardSummary) -> list[InsightCard]:
    """Cards derived directly from the aggregates. No interpretation, no model."""
    cards: list[InsightCard] = []

    untagged = next(
        (row for row in summary.by_category if row.category == "uncategorized"), None
    )
    if untagged and summary.spend:
        share = untagged.total / summary.spend
        if share >= _UNCATEGORIZED_ALERT_SHARE:
            cards.append(InsightCard(
                title=f"{share * 100:.0f}% of spending has no category",
                body=f"{rupees(untagged.total)} across {untagged.count} transactions is "
                     "uncategorized — mostly transfers to individuals. Tag the largest "
                     "few and the rest of this dashboard gets considerably more useful.",
                severity="warn",
                metric=rupees(untagged.total),
                generated_by="computed",
            ))

    spend_categories = [row for row in summary.by_category if row.category != "uncategorized"]
    if spend_categories:
        top = spend_categories[0]
        cards.append(InsightCard(
            title=f"Largest category: {top.category}",
            body=f"{rupees(top.total)} across {top.count} transactions, "
                 f"the biggest identified category this period.",
            severity="info",
            metric=rupees(top.total),
            generated_by="computed",
        ))

    if summary.net < 0:
        cards.append(InsightCard(
            title="Spending exceeded income this period",
            body=f"Net flow is {rupees(summary.net)}. Worth checking whether that is a "
                 "one-off large purchase or a steady gap.",
            severity="warn",
            metric=rupees(summary.net),
            generated_by="computed",
        ))
    else:
        cards.append(InsightCard(
            title=f"Net surplus of {rupees(summary.net)}",
            body="More came in than went out over this window. Note that transfers "
                 "between your own accounts can inflate both sides.",
            severity="good",
            metric=rupees(summary.net),
            generated_by="computed",
        ))

    if summary.needs_review:
        cards.append(InsightCard(
            title=f"{summary.needs_review} transactions need a look",
            body="Either the model was unsure or it could not be reached. Reviewing "
                 "them in bulk on the Transactions page is usually a few minutes' work.",
            severity="info",
            metric=str(summary.needs_review),
            generated_by="computed",
        ))

    owed = max(
        (row for row in summary.people if row.they_owe_you > 0),
        key=lambda row: row.they_owe_you, default=None,
    )
    if owed:
        cards.append(InsightCard(
            title=f"{owed.person.name} is net {rupees(owed.they_owe_you)} behind",
            body=f"Across {owed.transaction_count} transactions in both directions. "
                 "This is a running total, not a formal debt.",
            severity="info",
            metric=rupees(owed.they_owe_you),
            generated_by="computed",
        ))

    return cards[:5]
