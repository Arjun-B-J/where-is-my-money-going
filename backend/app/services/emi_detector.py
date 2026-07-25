"""EMI / loan-conversion detector.

Identifies two kinds of installment activity:

1. **Card-side EMI conversions** — HDFC credit-card statements list EMI rows
   like "RAZ*Toothsi - <i>/<n>" or "Principal Amount Amortization - <i>/<n>".
   We pick those out and group them by merchant.
2. **Statement-period EMI** — same merchant + same amount appearing month
   after month at fixed cadence on the same source. The recurring detector
   already finds these; the EMI detector annotates them with progress
   (`installment_<i>/<n>`) when the pattern is parseable.

This is a deterministic detector — no LLM required. The narrative prose pass
will use these structured findings to write the EMI-narrative paragraph.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Transaction, TxnDirection

# Matches HDFC EMI conversion rows like:
#   "Principal Amount Amortization - <2/9>RAZ*Toothsi"
#   "RAZ*Toothsi - 2/9"
#   "Game Nation EMI conversion 1/3"
EMI_TAG_RE = re.compile(
    r"\b(?:<\s*)?(?P<i>\d{1,2})\s*[/\-]\s*(?P<n>\d{1,2})(?:\s*>)?",
)
PRINCIPAL_RE = re.compile(r"(?i)\bprincipal\s+amount\s+amortization", re.IGNORECASE)
INTEREST_RE = re.compile(r"(?i)\binterest\s+amount\s+amortization", re.IGNORECASE)
IGST_RE = re.compile(r"(?i)\bIGST[-\s]?CI", re.IGNORECASE)


@dataclass
class EMIInstallment:
    merchant: str
    source: str
    txn_id: int
    posted_at: datetime
    amount: float
    installment_index: int | None
    installment_total: int | None
    component: str               # "principal" | "interest" | "igst" | "single"


@dataclass
class EMIPlan:
    merchant: str
    source: str
    monthly_amount: float        # principal-only median when available, else gross median
    installments_seen: int
    installments_total: int | None
    first_seen: datetime
    last_seen: datetime
    progress_label: str          # e.g. "8/9 paid"
    total_paid: float
    completed: bool


def _normalize_merchant(desc: str) -> str:
    """Extract a clean merchant name from an EMI-row description."""
    s = re.sub(r"(?i)principal amount amortization\s*-?\s*", "", desc)
    s = re.sub(r"(?i)interest amount amortization\s*-?\s*", "", s)
    s = re.sub(r"(?i)IGST[-\s]?CI@\d+%", "", s).strip()
    s = re.sub(r"<\s*\d+\s*/\s*\d+\s*>", "", s).strip()
    s = re.sub(r"^(RAZ\*|PYU\*|IND\*|POS\s+)", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+\d+\s*/\s*\d+\s*$", "", s).strip()
    return s[:60] or "Unknown EMI"


def detect_emi_installments(db: Session) -> list[EMIInstallment]:
    """Return every transaction that looks like an EMI installment row."""
    txns = (
        db.query(Transaction)
        .filter(Transaction.is_duplicate.is_(False))
        .order_by(Transaction.posted_at.asc())
        .all()
    )
    out: list[EMIInstallment] = []
    for t in txns:
        desc = t.raw_description or ""
        is_principal = bool(PRINCIPAL_RE.search(desc))
        is_interest = bool(INTEREST_RE.search(desc))
        is_igst = bool(IGST_RE.search(desc))

        # Try to find an explicit i/n token — but ignore IGST percent numbers
        i_n = None
        if not is_igst:
            m = EMI_TAG_RE.search(desc)
            if m:
                try:
                    i, n = int(m.group("i")), int(m.group("n"))
                    if 1 <= n <= 24 and 1 <= i <= n:
                        i_n = (i, n)
                except ValueError:
                    pass

        # An EMI row is either:
        #  - an explicit principal/interest amortization line
        #  - has an "<i/n>" tag on a card-source debit
        if not (is_principal or is_interest or is_igst or i_n):
            continue
        if t.direction != TxnDirection.DEBIT:
            continue

        component = (
            "principal" if is_principal
            else "interest" if is_interest
            else "igst" if is_igst
            else "single"
        )
        out.append(EMIInstallment(
            merchant=_normalize_merchant(desc),
            source=t.source.value,
            txn_id=t.id,
            posted_at=t.posted_at,
            amount=t.amount,
            installment_index=i_n[0] if i_n else None,
            installment_total=i_n[1] if i_n else None,
            component=component,
        ))
    return out


def summarize_emi_plans(db: Session) -> list[EMIPlan]:
    """Group installments by merchant, build one EMIPlan per loan."""
    inst = detect_emi_installments(db)
    if not inst:
        return []

    by_merchant: dict[tuple[str, str], list[EMIInstallment]] = defaultdict(list)
    for i in inst:
        by_merchant[(i.merchant, i.source)].append(i)

    plans: list[EMIPlan] = []
    for (merchant, source), group in by_merchant.items():
        group.sort(key=lambda x: x.posted_at)
        # Use principal rows for the headline monthly amount when available
        principal_rows = [g for g in group if g.component == "principal"]
        amount_source = principal_rows or group
        amounts = sorted(a.amount for a in amount_source)
        median_amount = amounts[len(amounts) // 2] if amounts else 0
        # Total = sum of all components (principal + interest + igst)
        total_paid = sum(g.amount for g in group)

        # Find a known total from any tagged row
        n_total = None
        i_max = 0
        for g in group:
            if g.installment_total:
                n_total = max(n_total or 0, g.installment_total)
            if g.installment_index:
                i_max = max(i_max, g.installment_index)

        # Distinct calendar months touched
        months_seen = {g.posted_at.strftime("%Y-%m") for g in group
                       if g.component != "igst"}
        installments_seen = i_max or len(months_seen) or 1

        completed = bool(n_total and installments_seen >= n_total)
        if n_total:
            progress = f"{installments_seen}/{n_total} {'completed' if completed else 'paid'}"
        else:
            progress = f"{installments_seen} installments seen"

        plans.append(EMIPlan(
            merchant=merchant,
            source=source,
            monthly_amount=round(median_amount, 2),
            installments_seen=installments_seen,
            installments_total=n_total,
            first_seen=min(g.posted_at for g in group),
            last_seen=max(g.posted_at for g in group),
            progress_label=progress,
            total_paid=round(total_paid, 2),
            completed=completed,
        ))

    plans.sort(key=lambda p: -p.total_paid)
    return plans
