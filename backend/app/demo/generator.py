"""Synthetic transaction generator for demos, screenshots and tests.

Everything here is invented. The names are fictional and the phone numbers use a
deliberately unassignable `90000 000xx` pattern, so nobody real can be contacted
by reading this file — the whole point of shipping a synthetic dataset is that
the demo never touches anyone's actual statements.

The data is shaped to exercise the parts of the app that matter:

* recurring monthly commitments, so the subscription and instalment detectors
  have something to find
* two-way flows with the same people, so friend detection has something to find
* deliberately opaque payees, so the review queue is non-empty and confidence
  scores spread out instead of all sitting at 1.0
* a running balance on the account statement, because direction inference for
  real PDFs depends on it

Deterministic for a given seed, which is what makes the tests and the
screenshots reproducible.
"""
from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ingest.records import ParsedTxn
from app.models import TxnDirection, TxnSource

AmountRange = tuple[int, int]


@dataclass(frozen=True)
class Flow:
    """One direction of repeated money movement with a person."""

    label: str
    amount_range: AmountRange
    per_year: int


@dataclass(frozen=True)
class Friend:
    """A fictional person the account holder exchanges money with."""

    name: str
    vpa: str
    sends: list[Flow] = field(default_factory=list)
    receives: list[Flow] = field(default_factory=list)


@dataclass(frozen=True)
class Recurring:
    """A monthly commitment."""

    label: str
    amount: int
    day: int
    direction: TxnDirection
    source: TxnSource
    description: str
    merchant: str | None = None
    vpa: str | None = None


@dataclass(frozen=True)
class Payee:
    """A merchant that shows up in day-to-day spending."""

    merchant: str
    description: str
    amount_range: AmountRange


@dataclass(frozen=True)
class SpendGroup:
    """A weighted group of everyday payees on one account."""

    weight: float
    source: TxnSource
    payees: list[Payee]


@dataclass(frozen=True)
class Instalments:
    """A purchase converted to EMI, billed monthly with a visible counter."""

    merchant: str
    monthly: float
    count: int
    day: int


# Unmistakably fictional, on purpose. Recognisable names make it obvious at a
# glance that this dataset is invented — which is worth more than plausibility
# for a demo whose screenshots end up in a public README.
FRIENDS = [
    Friend(
        name="LIONEL MESSI", vpa="lionelmessi@okbank",
        # Lends, then gets paid back in instalments.
        sends=[Flow("LOAN TO FRIEND", (5_000, 15_000), 3)],
        receives=[Flow("REPAYMENT", (3_000, 9_000), 5)],
    ),
    Friend(
        # The emoji is deliberate, and it earns its keep: a payee name can carry
        # any character a bank lets through, and this one is not in the report's
        # font. It exercises `reports.theme.pdf_safe` and the friend detector's
        # name normalisation on every demo run.
        name="CRISTIANO RONALDO 🐐", vpa="cristianoronaldo@okbank",
        # The other direction: borrowed once, repaying steadily.
        sends=[Flow("REPAYMENT", (4_000, 10_000), 4)],
        receives=[Flow("LOAN FROM FRIEND", (12_000, 20_000), 1)],
    ),
    Friend(
        name="KYLIAN MBAPPE", vpa="kylianmbappe@okbank",
        # Flatmate: constant small two-way settling.
        sends=[Flow("SPLIT BILL", (250, 1_600), 14)],
        receives=[Flow("SPLIT BILL", (200, 1_400), 12)],
    ),
]

RECURRING = [
    Recurring("Salary", 165_000, 1, TxnDirection.CREDIT, TxnSource.BANK,
              "NEFT-CR-EXAMPLE EMPLOYER PVT LTD-SALARY", "EXAMPLE EMPLOYER PVT LTD"),
    Recurring("Rent", 32_000, 3, TxnDirection.DEBIT, TxnSource.BANK,
              "UPI-PEP GUARDIOLA-pepguardiola@okbank-RENT", "PEP GUARDIOLA",
              "pepguardiola@okbank"),
    Recurring("Electricity", 2_400, 5, TxnDirection.DEBIT, TxnSource.BANK,
              "BBPS STATE ELECTRICITY BOARD", "STATE ELECTRICITY BOARD"),
    Recurring("Broadband", 1_199, 6, TxnDirection.DEBIT, TxnSource.BANK,
              "BBPS EXAMPLE FIBERNET BROADBAND", "EXAMPLE FIBERNET"),
    Recurring("Home services", 7_000, 5, TxnDirection.DEBIT, TxnSource.UPI,
              "UPI-HOME SERVICES-homeservices@okbank-MONTHLY", "HOME SERVICES",
              "homeservices@okbank"),
    Recurring("Index fund SIP", 25_000, 7, TxnDirection.DEBIT, TxnSource.BANK,
              "ACH-D-EXAMPLE BROKING-SIP INDEX FUND", "EXAMPLE BROKING"),
    Recurring("Gym", 1_999, 10, TxnDirection.DEBIT, TxnSource.CARD,
              "POS EXAMPLE FITNESS CLUB", "EXAMPLE FITNESS CLUB"),
    Recurring("Music streaming", 119, 15, TxnDirection.DEBIT, TxnSource.CARD,
              "POS EXAMPLE MUSIC PREMIUM", "EXAMPLE MUSIC"),
    Recurring("Video streaming", 649, 20, TxnDirection.DEBIT, TxnSource.CARD,
              "POS EXAMPLE STREAMING", "EXAMPLE STREAMING"),
    Recurring("Card bill payment", 50_000, 25, TxnDirection.DEBIT, TxnSource.BANK,
              "ACH-D-CREDIT CARD AUTO PAY", "CREDIT CARD PAYMENT"),
]

EVERYDAY = [
    SpendGroup(0.26, TxnSource.UPI, [
        Payee("EXAMPLE FOOD DELIVERY", "UPI-EXAMPLE FOOD DELIVERY-food@okbank", (180, 850)),
        Payee("CORNER RESTAURANT", "UPI-CORNER RESTAURANT-corner@okbank", (220, 950)),
        Payee("OFFICE CAFETERIA", "UPI-OFFICE CAFETERIA-cafe@okbank", (30, 220)),
    ]),
    SpendGroup(0.16, TxnSource.UPI, [
        Payee("EXAMPLE CABS", "UPI-EXAMPLE CABS-cabs@okbank", (95, 480)),
        Payee("EXAMPLE BIKE TAXI", "UPI-EXAMPLE BIKE TAXI-bike@okbank", (45, 180)),
        Payee("CITY METRO", "POS CITY METRO RAIL", (20, 80)),
    ]),
    SpendGroup(0.13, TxnSource.CARD, [
        Payee("EXAMPLE SUPERMARKET", "POS EXAMPLE SUPERMARKET", (450, 2_800)),
        Payee("QUICK GROCERY", "UPI-QUICK GROCERY-grocery@okbank", (180, 1_200)),
    ]),
    SpendGroup(0.09, TxnSource.CARD, [
        Payee("EXAMPLE MARKETPLACE", "POS EXAMPLE MARKETPLACE", (299, 4_500)),
        Payee("EXAMPLE FASHION", "POS EXAMPLE FASHION", (599, 2_900)),
        Payee("EXAMPLE SPORTS", "POS EXAMPLE SPORTS STORE", (1_200, 5_500)),
    ]),
    SpendGroup(0.06, TxnSource.CARD, [
        Payee("EXAMPLE FUEL", "POS EXAMPLE FUEL STATION", (1_500, 3_200)),
    ]),
    SpendGroup(0.05, TxnSource.CARD, [
        Payee("EXAMPLE CINEMA", "POS EXAMPLE CINEMA", (250, 1_500)),
    ]),
    SpendGroup(0.05, TxnSource.UPI, [
        Payee("EXAMPLE PHARMACY", "UPI-EXAMPLE PHARMACY-pharmacy@okbank", (180, 1_400)),
    ]),
    SpendGroup(0.06, TxnSource.CARD, [
        Payee("EXAMPLE COFFEE", "POS EXAMPLE COFFEE ROASTERS", (180, 650)),
    ]),
    SpendGroup(0.02, TxnSource.BANK, [
        Payee("ATM WITHDRAWAL", "ATM-WDL-BRANCH 0234", (2_000, 10_000)),
    ]),
]

# Payees no rule can match and no model should confidently classify. These make
# the review queue meaningful — a demo where every row is tagged at 100%
# confidence demonstrates nothing.
OPAQUE = [
    ("UPI-S KUMAR-skumar@okbank", 220.0),
    ("UPI-R PRASAD-rprasad@okbank", 85.0),
    ("POS UNLISTED MERCHANT ID 4829", 540.0),
    ("UPI-9000000042@okbank", 1_500.0),
]

EMI = Instalments("EXAMPLE ELECTRONICS", 4_499.0, 9, 12)

# Only accounts that print a closing balance get one; card and UPI exports do not.
OPENING_BALANCE = {TxnSource.BANK: 180_000.0}

# Average number of everyday transactions per day, and its spread.
#
# These, the salary and the card-bill amount are tuned together so the synthetic
# household saves roughly a fifth of its income and the card bill is close to the
# card spending it settles. A demo that spends more than it earns makes the
# dashboard read as a warning about the fake data rather than a demonstration of
# the app, and one where the card payment does not match card spend makes the
# cross-statement reconciliation look broken.
_DAILY_MEAN = 1.7
_DAILY_STDDEV = 1.0


def _month_start(reference: datetime, months_back: int) -> datetime:
    """First day of the month `months_back` before `reference`.

    Calendar arithmetic, not 30-day steps: stepping by 30 days drifts about five
    days a year and eventually yields two buckets for the same month.
    """
    year, month = reference.year, reference.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, 12, 0, 0)


def _on_day(month: datetime, day: int, rng: random.Random) -> datetime:
    """A timestamp on `day` of `month`, clamped to that month's real length."""
    last_day = calendar.monthrange(month.year, month.month)[1]
    return month.replace(day=min(day, last_day)) + timedelta(
        hours=rng.randint(8, 21), minutes=rng.randint(0, 59)
    )


def generate_transactions(
    seed: int = 42,
    months: int = 12,
    end_date: datetime | None = None,
) -> list[ParsedTxn]:
    """Build a deterministic synthetic history, oldest first."""
    rng = random.Random(seed)
    end = (end_date or datetime.now()).replace(hour=12, minute=0, second=0, microsecond=0)
    first_month = _month_start(end, months - 1)

    txns: list[ParsedTxn] = []

    def add(
        posted: datetime,
        amount: float,
        direction: TxnDirection,
        source: TxnSource,
        description: str,
        merchant: str | None = None,
        vpa: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        txns.append(ParsedTxn(
            posted_at=posted,
            amount=round(amount, 2),
            direction=direction,
            source=source,
            raw_description=description,
            merchant_normalized=merchant,
            counterparty_id=vpa,
            extra_metadata={"synthetic": True, **(metadata or {})},
        ))

    # ---- monthly commitments ----
    for offset in range(months):
        month = _month_start(end, months - 1 - offset)

        for item in RECURRING:
            posted = _on_day(month, item.day, rng)
            if posted > end:
                continue
            # A little jitter so medians and cadence detection have real variance
            # to work with rather than a column of identical amounts.
            amount = item.amount * (1 + rng.uniform(-0.02, 0.02))
            add(posted, amount, item.direction, item.source, item.description,
                item.merchant, item.vpa, {"recurring": item.label})

        instalment = offset + 1
        if instalment <= EMI.count:
            posted = _on_day(month, EMI.day, rng)
            if posted <= end:
                add(
                    posted, EMI.monthly, TxnDirection.DEBIT, TxnSource.CARD,
                    f"EMI {EMI.merchant} Principal Amount Amortization - "
                    f"{instalment}/{EMI.count}",
                    EMI.merchant, None,
                    {"emi_instalment": instalment, "emi_total": EMI.count},
                )

    # ---- everyday spending ----
    weights = [group.weight for group in EVERYDAY]
    day = first_month
    while day <= end:
        for _ in range(max(0, round(rng.gauss(_DAILY_MEAN, _DAILY_STDDEV)))):
            group = rng.choices(EVERYDAY, weights=weights, k=1)[0]
            payee = rng.choice(group.payees)
            posted = day + timedelta(
                hours=rng.randint(7, 23), minutes=rng.randint(0, 59),
                seconds=rng.randint(0, 59),
            )
            if posted > end:
                continue
            low, high = payee.amount_range
            add(posted, rng.uniform(low, high), TxnDirection.DEBIT, group.source,
                payee.description, payee.merchant)
        day += timedelta(days=1)

    # ---- two-way flows with people ----
    span_days = max((end - first_month).days, 1)
    for friend in FRIENDS:
        for direction, flows in (
            (TxnDirection.DEBIT, friend.sends),
            (TxnDirection.CREDIT, friend.receives),
        ):
            for flow in flows:
                for _ in range(max(1, round(flow.per_year * months / 12))):
                    posted = first_month + timedelta(
                        days=rng.randint(0, span_days),
                        hours=rng.randint(9, 22), minutes=rng.randint(0, 59),
                    )
                    if posted > end:
                        continue
                    low, high = flow.amount_range
                    add(posted, rng.uniform(low, high), direction, TxnSource.UPI,
                        f"UPI-{friend.name}-{friend.vpa}-{flow.label}",
                        friend.name, friend.vpa, {"person": friend.name})

    # ---- opaque payees ----
    for description, base_amount in OPAQUE:
        for _ in range(rng.randint(2, 5)):
            posted = first_month + timedelta(
                days=rng.randint(0, span_days), hours=rng.randint(9, 22),
                minutes=rng.randint(0, 59),
            )
            if posted > end:
                continue
            add(posted, base_amount * (1 + rng.uniform(-0.05, 0.05)),
                TxnDirection.DEBIT, TxnSource.UPI, description, None, None,
                {"opaque": True})

    txns.sort(key=lambda t: t.posted_at)

    # The running balance has to be applied after sorting, in real chronological
    # order. Otherwise the balance column contradicts the timeline and the
    # balance-delta direction inference this data exists to exercise breaks.
    balance = dict(OPENING_BALANCE)
    for txn in txns:
        if txn.source not in balance:
            continue
        delta = txn.amount if txn.direction == TxnDirection.CREDIT else -txn.amount
        balance[txn.source] = round(balance[txn.source] + delta, 2)
        txn.balance_after = balance[txn.source]

    return txns
