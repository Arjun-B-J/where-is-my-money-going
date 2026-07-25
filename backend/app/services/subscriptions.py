"""Recurring-charge detection.

A subscription is a charge to the same payee, at a stable amount, on a regular
cadence. That is a property of the *pattern*, not of the merchant's name, so the
detector works on cadence and amount stability first and only uses brand names to
give a nicer label.

The previous version had it the other way round: it matched a list of fifteen
brand regexes and ignored everything else, so it found Netflix but missed the
gym, the water delivery and every regional service nobody had added to the list.
It also labelled anything matching `electricity` as one specific city's
distribution company, which was both wrong elsewhere and a detail about where the
author lives.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from sqlalchemy.orm import Session

from app.models import Transaction, TxnDirection

# Optional nicer labels for well-known services. Purely cosmetic: a payee that
# matches nothing here is still detected, it just keeps its own name.
BRAND_LABELS = [
    (re.compile(r"(?i)youtube|google\s+one"), "YouTube / Google One", "media"),
    (re.compile(r"(?i)apple\s*(services|com|one)|icloud"), "Apple services", "media"),
    (re.compile(r"(?i)spotify"), "Spotify", "media"),
    (re.compile(r"(?i)netflix"), "Netflix", "media"),
    (re.compile(r"(?i)prime\s+video|amazon\s+prime"), "Amazon Prime", "media"),
    (re.compile(r"(?i)hotstar|jiocinema|sonyliv|zee5"), "Streaming", "media"),
    (re.compile(r"(?i)microsoft|office\s*365|dropbox|notion|figma|github"), "Software", "media"),
    (re.compile(r"(?i)\bjio\b|airtel|\bvi\b|vodafone|bsnl"), "Mobile / telecom", "telecom"),
    (re.compile(r"(?i)fibernet|broadband|fibre|\bisp\b"), "Broadband", "broadband"),
    (re.compile(r"(?i)electric|power|energy"), "Electricity", "utility"),
    (re.compile(r"(?i)\bgas\b|\blpg\b"), "Gas", "utility"),
    (re.compile(r"(?i)water|purifier"), "Water", "utility"),
    (re.compile(r"(?i)cult\.?fit|gym|fitness"), "Gym / fitness", "fitness"),
    # Not `premium`: that word appears in half the streaming plans on earth.
    (re.compile(r"(?i)insurance|\bpolicy\b|assurance"), "Insurance", "insurance"),
]

# Categories that recur monthly but are not subscriptions. Rent, a card bill and a
# SIP are all regular, stable and monthly — and lumping them in would report an
# "annual subscription cost" several times anyone's actual subscription spend.
#
# This is why the detector runs *after* categorising rather than before: the
# category is a far better signal for "is this a service I subscribe to" than any
# amount of pattern matching on the payee name.
NOT_SUBSCRIPTIONS = frozenset({
    "rent",
    "loan_repayment",
    "loan_given",
    "loan_taken",
    "investments",
    "salary",
    "cash",
    # A regular transfer to a person is a standing arrangement, not a service.
    "uncategorized",
})

# Cadence windows, in days between consecutive charges.
_CADENCES = [
    ("weekly", 5, 9, 52),
    ("fortnightly", 12, 17, 26),
    ("monthly", 25, 36, 12),
    ("quarterly", 80, 100, 4),
    ("yearly", 350, 380, 1),
]

# A charge has to repeat this many times before a cadence means anything. Two
# points make a line through any pair of dates.
MIN_OCCURRENCES = 3

# How much the amount may vary and still count as "the same charge". Covers
# tariff changes, GST rounding and usage-linked utility bills.
MAX_AMOUNT_SPREAD = 0.25

# Gaps must be this consistent to be a cadence rather than a coincidence.
MAX_GAP_SPREAD = 0.35


@dataclass
class Subscription:
    service: str
    kind: str
    median_amount: float
    cadence: str
    occurrences: int
    source: str
    first_seen: datetime
    last_seen: datetime
    annual_estimate: float
    # True when the label came from BRAND_LABELS rather than the payee string.
    branded: bool = False


def _label(description: str, merchant: str | None) -> tuple[str, str] | None:
    haystack = f"{merchant or ''} {description or ''}"
    for pattern, name, kind in BRAND_LABELS:
        if pattern.search(haystack):
            return name, kind
    return None


def _spread(values: list[float]) -> float:
    """Relative spread around the median. 0 means every value is identical."""
    mid = median(values)
    if mid == 0:
        return 1.0
    return max(abs(value - mid) for value in values) / mid


def _cadence(gaps: list[int]) -> tuple[str, int] | None:
    """Classify the average gap, if the gaps are consistent enough."""
    if not gaps:
        return None
    average = sum(gaps) / len(gaps)
    if _spread([float(gap) for gap in gaps]) > MAX_GAP_SPREAD:
        return None
    for name, low, high, per_year in _CADENCES:
        if low <= average <= high:
            return name, per_year
    return None


def detect_subscriptions(db: Session) -> list[Subscription]:
    """Find charges that repeat at a regular cadence for a stable amount."""
    txns = (
        db.query(Transaction)
        .filter(
            Transaction.direction == TxnDirection.DEBIT,
            Transaction.is_duplicate.is_(False),
        )
        .order_by(Transaction.posted_at.asc())
        .all()
    )

    # Group by payee and account. The same service on two cards is two
    # subscriptions as far as the user is concerned.
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for txn in txns:
        if (txn.category or "uncategorized") in NOT_SUBSCRIPTIONS:
            continue
        # A standing transfer to someone you know is an arrangement with a person,
        # which the People view already covers.
        if txn.person_id is not None:
            continue
        payee = (txn.merchant_normalized or txn.raw_description or "").strip()
        if not payee:
            continue
        groups[(payee.upper(), txn.source.value)].append(txn)

    found: list[Subscription] = []
    for (payee, source), members in groups.items():
        if len(members) < MIN_OCCURRENCES:
            continue

        amounts = [m.amount for m in members]
        if _spread(amounts) > MAX_AMOUNT_SPREAD:
            # Varies too much to be a subscription — this is a shop you visit.
            continue

        gaps = [
            (members[i].posted_at - members[i - 1].posted_at).days
            for i in range(1, len(members))
        ]
        cadence = _cadence(gaps)
        if cadence is None:
            continue
        cadence_name, per_year = cadence

        brand = _label(members[0].raw_description, members[0].merchant_normalized)
        amount = median(amounts)

        found.append(Subscription(
            service=brand[0] if brand else payee.title(),
            kind=brand[1] if brand else "other",
            median_amount=round(amount, 2),
            cadence=cadence_name,
            occurrences=len(members),
            source=source,
            first_seen=members[0].posted_at,
            last_seen=members[-1].posted_at,
            annual_estimate=round(amount * per_year, 2),
            branded=brand is not None,
        ))

    found.sort(key=lambda sub: -sub.annual_estimate)
    return found
