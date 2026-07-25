"""The Spend Analysis PDF.

One report, assembled in this order:

    cover · executive summary · what was analysed · monthly flow ·
    one page per account · top payees · instalments · recurring charges ·
    anomalies · what your spending says about you

This replaces two earlier generators — a "dashboard digest" and an "editorial
report" — that between them duplicated their palette, chart helpers and table
styling across 1,500 lines. The digest also just restated the web dashboard, so
it is gone rather than merged.

Two honesty rules are enforced here rather than left to the prose:

* if a meaningful share of spending is uncategorised, the report says so on the
  executive summary instead of quietly reporting an "uncategorized" slice
* sections assembled from aggregates rather than written by the model are
  labelled as such
"""
from __future__ import annotations

import asyncio
import io
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from app.config import APP_NAME
from app.llm.client import LLMClient, get_llm
from app.models import Transaction, TxnDirection
from app.reports import charts
from app.reports.labels import PayeeAnnotator, source_label
from app.reports.narrative import (
    NarrativeBlock,
    behavioural_observations,
    build_source_summary,
    headline,
    narrate_source,
)
from app.reports.theme import (
    AMBER,
    AMBER_SOFT,
    AMBER_WASH,
    INK,
    INK_SOFT,
    MUTED,
    RULE,
    body_font,
    pdf_safe,
    percent,
    rupees,
    signed_rupees,
    styles,
)
from app.services.anomaly_hunter import all_anomalies
from app.services.budget import weekday_pattern
from app.services.cross_source import is_internal_transfer
from app.services.emi_detector import summarize_emi_plans
from app.services.subscriptions import detect_subscriptions

logger = logging.getLogger(__name__)

# Above this share of spend, an untagged pile is the report's headline problem
# and gets called out rather than shown as just another category.
_UNCATEGORIZED_NOTICE_THRESHOLD = 0.20

_MAX_PAYEE_ROWS = 14
_MAX_ANOMALIES = 12
_CONTENT_WIDTH = 17 * cm


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------


@dataclass
class PageChrome:
    """Text drawn onto the page canvas, outside the flowable story."""

    running_header: str = ""
    cover_footer: str = ""


def _draw_cover(canvas, doc, chrome: PageChrome) -> None:
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    canvas.setFillColor(AMBER)
    canvas.rect(0, A4[1] - 18 * mm, A4[0], 18 * mm, fill=1, stroke=0)

    canvas.setFillColor(INK_SOFT)
    canvas.rect(0, 0, A4[0], 26 * mm, fill=1, stroke=0)
    canvas.setFillColor(AMBER_SOFT)
    canvas.setFont(body_font(), 9)
    y = 15 * mm
    for line in chrome.cover_footer.split("\n"):
        canvas.drawCentredString(A4[0] / 2, y, line)
        y -= 4.6 * mm
    canvas.restoreState()


def _draw_interior(canvas, doc, chrome: PageChrome) -> None:
    canvas.saveState()
    canvas.setFillColor(AMBER)
    canvas.rect(0, A4[1] - 3.5 * mm, A4[0], 3.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont(body_font(), 8)
    canvas.drawString(20 * mm, 9 * mm, chrome.running_header)
    canvas.drawRightString(A4[0] - 20 * mm, 9 * mm, str(canvas.getPageNumber()))
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Table helper
# ---------------------------------------------------------------------------


def _table(rows: list[list], widths: list[float], s: dict) -> Table:
    """A styled table whose cells are Paragraphs so long text wraps.

    Every string goes through `pdf_safe`, because table cells are where
    statement-derived payee names land and those can contain anything.
    """
    header, *body = rows
    data = [[Paragraph(pdf_safe(str(cell)), s["cell_head"]) for cell in header]]
    for row in body:
        data.append([
            cell if isinstance(cell, Paragraph)
            else Paragraph(pdf_safe(str(cell)), s["cell"])
            for cell in row
        ])

    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, AMBER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [None, AMBER_WASH]),
        ("GRID", (0, 1), (-1, -1), 0.25, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


# ---------------------------------------------------------------------------
# Aggregates gathered once and passed around
# ---------------------------------------------------------------------------


@dataclass
class ReportData:
    txns: list[Transaction]
    sources: list[str]
    period_start: datetime
    period_end: datetime
    total_debit: float
    true_spend: float          # total_debit minus transfers between your own accounts
    internal_transfers: float
    total_credit: float
    salary_total: float
    card_debit: float
    uncategorized_debit: float
    monthly: list[tuple[str, float, float]]   # (label, money_in, money_out)
    headline: NarrativeBlock
    source_blocks: dict[str, NarrativeBlock]
    observations: list[dict]
    observations_origin: str

    @property
    def net(self) -> float:
        """Money in minus money genuinely spent.

        Uses `true_spend`, not `total_debit`: a card-bill payment appears on both
        the bank and the card statement, so subtracting every debit double-counts
        card spending and reports a deficit that does not exist.
        """
        return self.total_credit - self.true_spend

    @property
    def uncategorized_share(self) -> float:
        return self.uncategorized_debit / self.true_spend if self.true_spend else 0.0

    @property
    def period_label(self) -> str:
        return (
            f"{self.period_start.strftime('%b %Y')} – "
            f"{self.period_end.strftime('%b %Y')}"
        )


def _collect(db: Session) -> dict:
    """Everything computed from the database, with no model involvement."""
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()
    if not txns:
        return {"txns": []}

    debit = sum(t.amount for t in txns if t.direction == TxnDirection.DEBIT)
    credit = sum(t.amount for t in txns if t.direction == TxnDirection.CREDIT)

    # Salary is identified by category, not by matching an employer's payroll
    # descriptor. Searching for a specific employer's code would work for one
    # person and break for everyone else.
    salary = sum(
        t.amount for t in txns
        if t.direction == TxnDirection.CREDIT and t.category == "salary"
    )
    card_debit = sum(
        t.amount for t in txns
        if t.direction == TxnDirection.DEBIT and t.source.value.startswith("card")
    )

    # Paying a credit-card bill moves money between two accounts you own, and both
    # sides are in this dataset: a debit on the bank statement and the purchases it
    # settles on the card statement. Summing every debit therefore counts card
    # spending twice, which made "total spent" larger than income and produced a
    # net figure that looked alarming and was wrong.
    #
    # Real spending is every debit except the internal transfer.
    internal_transfers = sum(t.amount for t in txns if is_internal_transfer(t))
    true_spend = debit - internal_transfers
    uncategorized = sum(
        t.amount for t in txns
        if t.direction == TxnDirection.DEBIT and (t.category or "uncategorized") == "uncategorized"
    )

    flow: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for t in txns:
        bucket = flow[t.posted_at.strftime("%Y-%m")]
        if t.direction == TxnDirection.CREDIT:
            bucket[0] += t.amount
        else:
            bucket[1] += t.amount
    monthly = [
        (datetime.strptime(month, "%Y-%m").strftime("%b %y"), values[0], values[1])
        for month, values in sorted(flow.items())
    ]

    dates = [t.posted_at for t in txns]
    return {
        "txns": txns,
        "sources": sorted({t.source.value for t in txns}),
        "period_start": min(dates),
        "period_end": max(dates),
        "total_debit": debit,
        "true_spend": true_spend,
        "internal_transfers": internal_transfers,
        "total_credit": credit,
        "salary_total": salary,
        "card_debit": card_debit,
        "uncategorized_debit": uncategorized,
        "monthly": monthly,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def render_spend_analysis(db: Session, llm: LLMClient | None = None) -> bytes:
    """Generate the report and return PDF bytes.

    The model passes run concurrently where they are independent. The PDF build
    itself is CPU-bound and blocking, so it goes to a worker thread rather than
    stalling the event loop for the whole request.
    """
    llm = llm or get_llm()
    base = _collect(db)
    if not base["txns"]:
        return await asyncio.to_thread(_render_empty)

    # The opening paragraph and the per-account sections do not depend on each
    # other, so they are generated together.
    headline_block, *source_blocks = await asyncio.gather(
        headline(db, llm),
        *(narrate_source(db, source, llm) for source in base["sources"]),
    )
    observations, origin = await behavioural_observations(db, llm)

    data = ReportData(
        **base,
        headline=headline_block,
        source_blocks=dict(zip(base["sources"], source_blocks, strict=True)),
        observations=observations,
        observations_origin=origin,
    )
    return await asyncio.to_thread(_build, db, data)


def _render_empty() -> bytes:
    s = styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=f"{APP_NAME} · Spend Analysis")
    doc.build([
        Paragraph("Nothing to analyse yet", s["h1"]),
        Paragraph(
            "No transactions have been ingested. Add statements on the Ingest "
            "page, or load the demo dataset, then generate the report again.",
            s["body"],
        ),
    ])
    return buffer.getvalue()


def _build(db: Session, data: ReportData) -> bytes:
    s = styles()
    buffer = io.BytesIO()
    chrome = PageChrome(
        running_header=f"Spend analysis · {data.period_label}",
        cover_footer=(
            f"{len(data.txns):,} transactions across {len(data.sources)} account(s)\n"
            f"Generated {datetime.now().strftime('%d %B %Y')} · analysed locally"
        ),
    )

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=22 * mm, bottomMargin=18 * mm,
        title=f"{APP_NAME} · Spend Analysis",
        author=APP_NAME,
        subject=f"Personal spend analysis, {data.period_label}",
    )

    story: list = []
    story += _cover(data, s)
    story += _executive_summary(data, s)
    story += _what_was_analysed(data, s)
    story += _per_account_pages(db, data, s)
    story += _top_payees(db, data, s)
    story += _instalments_and_subscriptions(db, s)
    story += _anomalies(db, s)
    story += _observations(data, s)

    doc.build(
        story,
        onFirstPage=lambda c, d: _draw_cover(c, d, chrome),
        onLaterPages=lambda c, d: _draw_interior(c, d, chrome),
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _cover(data: ReportData, s: dict) -> list:
    return [
        Spacer(1, 52 * mm),
        Paragraph(APP_NAME.upper(), s["cover_eyebrow"]),
        Paragraph("Spend Analysis", s["cover_title"]),
        Paragraph(data.period_label, s["cover_period"]),
        Spacer(1, 6 * mm),
        Paragraph(
            "A transaction-level review of every statement ingested. All figures "
            "computed on this machine; nothing was uploaded anywhere.",
            s["cover_lede"],
        ),
        PageBreak(),
    ]


def _kpi_strip(data: ReportData, s: dict) -> Table:
    """Four headline numbers. Salary is omitted when nothing is tagged as salary."""
    tiles: list[tuple[str, str, str]] = []
    if data.salary_total:
        tiles.append(("Salary received", rupees(data.salary_total, compact=True), "kpi_value"))
    tiles.append(("Total spent", rupees(data.true_spend, compact=True), "kpi_value_accent"))
    if data.card_debit:
        tiles.append(("On credit cards", rupees(data.card_debit, compact=True), "kpi_value"))
    tiles.append((
        "Net change",
        signed_rupees(data.net, compact=True),
        "kpi_value_positive" if data.net >= 0 else "kpi_value_negative",
    ))
    tiles.append(("Transactions", f"{len(data.txns):,}", "kpi_value"))

    tiles = tiles[:4]
    table = Table(
        [
            [Paragraph(value, s[style]) for _, value, style in tiles],
            [Paragraph(label, s["kpi_label"]) for label, _, _ in tiles],
        ],
        colWidths=[_CONTENT_WIDTH / len(tiles)] * len(tiles),
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_WASH),
        ("BOX", (0, 0), (-1, -1), 0.5, AMBER_SOFT),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, AMBER_SOFT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    return table


def _executive_summary(data: ReportData, s: dict) -> list:
    story: list = [Paragraph("Executive summary", s["h1"]), _kpi_strip(data, s)]

    if data.internal_transfers:
        story.append(Paragraph(
            f"Total spent excludes {rupees(data.internal_transfers)} of card-bill "
            "payments, which move between your own accounts and appear on both "
            "statements. Counting them would report the same card spending twice.",
            s["caption"],
        ))
    story.append(Spacer(1, 12))

    # Say the uncomfortable thing first. A report that shows a 60% slice labelled
    # "uncategorized" without comment is technically accurate and useless.
    if data.uncategorized_share >= _UNCATEGORIZED_NOTICE_THRESHOLD:
        story.append(Paragraph(
            f"Read this first: {rupees(data.uncategorized_debit)} of "
            f"{rupees(data.true_spend)} "
            f"({data.uncategorized_share * 100:.0f}% of spending) has no category. "
            "Most of it is transfers to individuals, which cannot be labelled "
            "without knowing who they are. Every figure below that splits by "
            "category is understating the real totals by roughly that much.",
            s["notice"],
        ))
        story.append(Spacer(1, 6))

    story.append(Paragraph(pdf_safe(data.headline.body), s["body"]))

    for observation in data.observations[:2]:
        story.append(Paragraph(
            pdf_safe(f"<b>{observation['thesis']}</b> {observation['evidence']}"), s["body"]
        ))

    if data.monthly:
        story.append(Spacer(1, 8))
        story.append(Image(
            io.BytesIO(charts.monthly_flow(
                [label for label, _, _ in data.monthly],
                [money_in for _, money_in, _ in data.monthly],
                [money_out for _, _, money_out in data.monthly],
            )),
            width=_CONTENT_WIDTH, height=6.8 * cm,
        ))

    story.append(PageBreak())
    return story


def _what_was_analysed(data: ReportData, s: dict) -> list:
    by_source: dict[str, list[Transaction]] = defaultdict(list)
    for txn in data.txns:
        by_source[txn.source.value].append(txn)

    rows: list[list] = [["Account", "Period", "Transactions", "Mostly"]]
    for source in data.sources:
        group = by_source[source]
        spend: dict[str, float] = defaultdict(float)
        for txn in group:
            if txn.direction == TxnDirection.DEBIT:
                spend[txn.category or "uncategorized"] += txn.amount
        dominant = sorted(spend.items(), key=lambda kv: -kv[1])[:3]
        rows.append([
            source_label(source),
            f"{min(t.posted_at for t in group):%b %y} – {max(t.posted_at for t in group):%b %y}",
            f"{len(group):,}",
            ", ".join(name.replace("_", " ") for name, _ in dominant) or "—",
        ])

    story = [
        Paragraph("What was analysed", s["h1"]),
        _table(rows, [5.2 * cm, 3.4 * cm, 2.6 * cm, 5.8 * cm], s),
        Spacer(1, 10),
    ]

    trail = [(label, money_out) for label, _, money_out in data.monthly]
    if trail:
        story.append(Image(
            io.BytesIO(charts.outflow_trail(trail)), width=_CONTENT_WIDTH, height=6 * cm
        ))
    return story


def _per_account_pages(db: Session, data: ReportData, s: dict) -> list:
    story: list = []
    for source in data.sources:
        block = data.source_blocks.get(source)
        if block is None:
            continue
        summary = build_source_summary(db, source)

        story.append(PageBreak())
        story.append(Paragraph(source_label(source), s["h1"]))
        story.append(Paragraph(
            f"{summary.txn_count:,} transactions · {rupees(summary.debit_total)} out · "
            f"{rupees(summary.credit_total)} in",
            s["caption"],
        ))

        for paragraph in block.body.split("\n\n"):
            if paragraph.strip():
                story.append(Paragraph(pdf_safe(paragraph.strip()), s["body"]))
        if block.is_computed:
            story.append(Paragraph(
                "This section was assembled from the figures above rather than "
                "written by the local model.",
                s["caption"],
            ))

        if summary.top_categories:
            height = min(8.5, max(3.4, 1.4 + 0.55 * len(summary.top_categories)))
            story.append(Spacer(1, 8))
            story.append(Image(
                io.BytesIO(charts.category_donut(
                    summary.top_categories, f"Spend by category · {summary.label}"
                )),
                width=_CONTENT_WIDTH, height=height * cm,
            ))

        if summary.top_payees:
            rows: list[list] = [["Payee", "Total", "Share"]]
            for name, total in summary.top_payees[:8]:
                rows.append([
                    name[:40],
                    Paragraph(rupees(total), s["cell_right"]),
                    Paragraph(percent(total, summary.debit_total), s["cell_right"]),
                ])
            story.append(Paragraph("Largest payees on this account", s["h3"]))
            story.append(_table(rows, [9.4 * cm, 4 * cm, 3.6 * cm], s))
    return story


def _top_payees(db: Session, data: ReportData, s: dict) -> list:
    annotator = PayeeAnnotator.from_db(db)

    totals: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    for txn in data.txns:
        if txn.direction != TxnDirection.DEBIT:
            continue
        key = txn.merchant_normalized or txn.raw_description[:40] or "unknown"
        totals[key]["total"] += txn.amount
        totals[key]["count"] += 1

    ranked = sorted(totals.items(), key=lambda kv: -kv[1]["total"])[:_MAX_PAYEE_ROWS]
    rows: list[list] = [["Payee", "Total", "Count", "Note"]]
    for name, info in ranked:
        rows.append([
            name[:38],
            Paragraph(rupees(info["total"]), s["cell_right"]),
            Paragraph(str(info["count"]), s["cell_right"]),
            annotator.note_for(name, txn_count=info["count"]),
        ])

    return [
        PageBreak(),
        Paragraph("Where the money went", s["h1"]),
        Paragraph(
            "Ranked by total spend. Notes come from your own payee list — add to "
            "it to annotate the rows that matter to you.",
            s["caption"],
        ),
        _table(rows, [5.4 * cm, 3 * cm, 1.8 * cm, 6.8 * cm], s),
    ]


def _instalments_and_subscriptions(db: Session, s: dict) -> list:
    story: list = []

    plans = summarize_emi_plans(db)
    if plans:
        rows: list[list] = [["Purchase", "Account", "Monthly", "Progress", "Paid so far"]]
        for plan in plans[:12]:
            rows.append([
                plan.merchant[:32],
                source_label(plan.source),
                Paragraph(rupees(plan.monthly_amount), s["cell_right"]),
                plan.progress_label,
                Paragraph(rupees(plan.total_paid), s["cell_right"]),
            ])
        story += [
            PageBreak(),
            Paragraph("Instalment plans", s["h1"]),
            Paragraph(
                "Purchases converted to EMI, detected from the instalment counters "
                "printed on card statements.",
                s["caption"],
            ),
            _table(rows, [4.4 * cm, 3.2 * cm, 2.6 * cm, 3.4 * cm, 3.4 * cm], s),
        ]

    subs = detect_subscriptions(db)
    if subs:
        annual = sum(sub.annual_estimate for sub in subs)
        rows = [["Service", "Amount", "Cadence", "Seen", "Account"]]
        for sub in subs[:14]:
            rows.append([
                sub.service[:30],
                Paragraph(rupees(sub.median_amount), s["cell_right"]),
                sub.cadence,
                f"{sub.occurrences}×",
                source_label(sub.source),
            ])
        story += [
            Spacer(1, 14),
            Paragraph("Recurring charges", s["h1"]),
            Paragraph(
                f"About {rupees(annual / 12)} a month, {rupees(annual)} a year, "
                f"across {len(subs)} recurring charges.",
                s["body"],
            ),
            _table(rows, [4.6 * cm, 2.8 * cm, 2.8 * cm, 1.8 * cm, 4 * cm], s),
        ]

    pattern = weekday_pattern(db)
    if any(row.debit_total for row in pattern):
        totals = {row.weekday[:3]: row.debit_total for row in pattern}
        story += [
            Spacer(1, 14),
            Image(io.BytesIO(charts.weekday_pattern(totals)),
                  width=_CONTENT_WIDTH, height=5.2 * cm),
        ]
    return story


def _anomalies(db: Session, s: dict) -> list:
    hits = all_anomalies(db)
    if not hits:
        return []

    story: list = [
        PageBreak(),
        Paragraph("Worth checking", s["h1"]),
        Paragraph(
            "Deterministic checks for duplicate charges, refunds that never "
            "arrived, clusters of tiny debits and same-day round trips. These are "
            "flags, not conclusions.",
            s["caption"],
        ),
    ]
    for hit in hits[:_MAX_ANOMALIES]:
        story.append(KeepTogether([
            Paragraph(
                f"{hit.title} · <font color='#64748B'>"
                f"{hit.posted_at.strftime('%d %b %Y')}</font>",
                s["h3"],
            ),
            Paragraph(hit.detail, s["body"]),
        ]))
    return story


def _observations(data: ReportData, s: dict) -> list:
    if not data.observations:
        return []

    story: list = [
        PageBreak(),
        Paragraph("What your spending says about you", s["h1"]),
    ]
    if data.observations_origin == "computed":
        story.append(Paragraph(
            "Assembled from the detectors below rather than written by the local "
            "model, which was unavailable when this report was generated.",
            s["caption"],
        ))
    for index, observation in enumerate(data.observations, start=1):
        story.append(Paragraph(
            pdf_safe(f"<b>{index}. {observation['thesis']}</b> {observation['evidence']}"),
            s["body"],
        ))
        story.append(Spacer(1, 4))
    return story
