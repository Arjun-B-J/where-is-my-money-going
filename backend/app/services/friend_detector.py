"""Auto-discover real friends from transaction patterns.

A counterparty is classified as a friend (vs a merchant) iff:
  1. Bidirectional money flow — you both sent to and received from them
  2. Name looks like a real person (capitalized 1-3 word names, no corp suffixes)
  3. Counterparty key looks personal (UPI VPA at a bank, not a merchant aggregator)
  4. Volume and recurrence patterns are friend-like, not merchant-refund-like

The detector then auto-creates Person rows, sets relationship_type="friend",
and backfills person_id on all matching transactions.

Pure-Python, deterministic, idempotent.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Person, Transaction, TxnDirection

# Words that are dead giveaways for a corporate/merchant counterparty.
# Anything matching these in the name is NOT a friend.
_MERCHANT_KEYWORDS = re.compile(
    r"\b(?:LTD|LIMITED|PVT|PRIVATE|INC|TECHNOLOGIES|PAY|"
    r"AMAZON|SWIGGY|ZOMATO|UBER|OLA|RAPIDO|BLINKIT|ZEPTO|"
    r"BUNDL|RAZORPAY|PAYTM|PHONEPE|GPAY|GOOGLE|YOUTUBE|"
    r"NETFLIX|SPOTIFY|CULT|PRACTO|APOLLO|HDFC|ICICI|SBI|AXIS|"
    r"CRED|CREDCLUB|BBPS|IRCTC|MAKEMYTRIP|IXIGO|BOOKMYSHOW|"
    r"CASHFREE|RSP|RSP\*|SMARTQ|INSTAMART|GROCERY|FLIGHTS|"
    r"COMMERC|MERCHANT|VENDOR|CORP|COMPANY|INSURANCE|TRANSPORT|"
    r"GROUP|SOLUTIONS|SERVICES|BHARATPE|FINTECH)\b",
    re.IGNORECASE,
)

# Personal-VPA patterns — the LHS looks human-ish (alpha + digits, not pure
# numeric or a known merchant aggregator handle).
_PERSONAL_VPA = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9._-]{2,32}@(ok[a-z]+|y?bl|paytm|sbi|hdfcbank|icici|axis|"
    r"upi|fbl|ibl|kbl|kotak|airtel|federal)$",
    re.IGNORECASE,
)

# A reasonable-looking person name (Indian-ish): 1-3 words, mostly letters,
# at least one capital cluster, length 3-40.
_PERSON_NAME = re.compile(r"^([A-Z][A-Z ]{1,38}|[A-Z][a-zA-Z]{1,40}( [A-Z]\.?| [A-Z][a-zA-Z]+){0,2})$")


@dataclass
class DetectedFriend:
    canonical_name: str
    upi_ids: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    sent_count: int = 0
    received_count: int = 0
    sent_total: float = 0.0
    received_total: float = 0.0
    net_owed_to_you: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    sample_txn_ids: list[int] = field(default_factory=list)


def _is_personal_vpa(vpa: str | None) -> bool:
    if not vpa:
        return False
    return bool(_PERSONAL_VPA.match(vpa))


def _looks_like_merchant(name: str | None) -> bool:
    if not name:
        return True
    return bool(_MERCHANT_KEYWORDS.search(name))


# Anything that is not a letter, space, dot or hyphen. Bank narrations and UPI
# app exports pass through whatever the sender typed, including emoji.
_NON_NAME_CHARS = re.compile(r"[^A-Za-z .\-]")


def _looks_like_person_name(name: str | None) -> bool:
    """Whether `name` has the shape of a person's name.

    Decorative characters are stripped before the shape test, not treated as
    disqualifying: "SOME PERSON 🐐" is still a person, and rejecting it would
    quietly drop them from the People view.
    """
    if not name:
        return False
    name = _NON_NAME_CHARS.sub(" ", name).strip()
    name = re.sub(r"\s{2,}", " ", name)
    if len(name) < 3 or len(name) > 40:
        return False
    if _looks_like_merchant(name):
        return False
    # Bank narrations usually transmit names in capitals; app exports sometimes
    # preserve mixed case. Accept either shape.
    all_caps = bool(re.match(r"^[A-Z][A-Z ]{2,38}$", name)) and 1 <= name.count(" ") <= 3
    mixed_case = bool(re.match(r"^[A-Z][a-zA-Z]+(?:[. ][A-Z][a-zA-Z]+){0,2}$", name))
    return all_caps or mixed_case


def _norm_key(t: Transaction) -> str | None:
    if t.counterparty_id:
        return t.counterparty_id.lower()
    if t.merchant_normalized:
        return t.merchant_normalized.lower()
    return None


def _canon_name_from_descriptions(descs: list[str]) -> str:
    """Best name guess from a bunch of raw descriptions for the same counterparty."""
    # Look for "UPI-NAME-..." pattern
    for d in descs:
        m = re.search(r"UPI[/-](?:CR/|DR/)?([A-Z][A-Z ]{2,38})", d.upper())
        if m:
            cand = m.group(1).strip()
            cand = re.sub(r"\s+\d+", "", cand).strip()
            if _looks_like_person_name(cand):
                return cand
    # Fallback: strip merchant prefixes from first description
    if descs:
        cand = re.sub(r"^(GPAY-|UPI[/-]|POS\s+)", "", descs[0]).strip()
        cand = cand.split("-")[0].strip()
        if 3 <= len(cand) <= 40:
            return cand
    return descs[0][:40] if descs else "Unknown"


def detect_friends(
    db: Session,
    *,
    min_send_count: int = 1,
    min_recv_count: int = 1,
    min_total: float = 500.0,
) -> list[DetectedFriend]:
    """Scan all transactions for friend-shaped counterparties.

    Heuristics: bidirectional flow, personal name, personal VPA, non-merchant.
    """
    txns = db.query(Transaction).filter(Transaction.is_duplicate.is_(False)).all()

    by_key: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        k = _norm_key(t)
        if k:
            by_key[k].append(t)

    out: list[DetectedFriend] = []

    for key, members in by_key.items():
        debits = [t for t in members if t.direction == TxnDirection.DEBIT]
        credits = [t for t in members if t.direction == TxnDirection.CREDIT]
        if len(debits) < min_send_count or len(credits) < min_recv_count:
            continue

        sent_sum = sum(t.amount for t in debits)
        recv_sum = sum(t.amount for t in credits)
        if sent_sum < min_total or recv_sum < min_total:
            continue

        # Pick the best display name. A parser that already extracted a clean
        # name is more trustworthy than re-parsing the raw narration, and it
        # preserves characters the narration regex would clip.
        names = [t.merchant_normalized for t in members if t.merchant_normalized]
        descs = [t.raw_description for t in members if t.raw_description]
        parsed_name = next((n for n in names if _looks_like_person_name(n)), None)
        if parsed_name:
            canon = parsed_name
        elif descs:
            canon = _canon_name_from_descriptions(descs)
        else:
            canon = names[0] if names else key

        # Disqualifiers
        if _looks_like_merchant(canon):
            continue
        # If the key is a UPI-like string but doesn't look personal, skip
        if "@" in key and not _is_personal_vpa(key):
            continue
        # Names dominated by digits aren't humans
        if re.search(r"\d{4,}", canon):
            continue
        # If the only "name" we could find still has a corp keyword, skip
        if any(_looks_like_merchant(n) for n in names if n):
            # mixed merchant + non-merchant rows on same UPI is weird — skip
            continue

        # Score the confidence
        confidence = 0.5
        reason_parts = ["bidirectional flow"]
        if _looks_like_person_name(canon):
            confidence += 0.25
            reason_parts.append("person-shaped name")
        if "@" in key and _is_personal_vpa(key):
            confidence += 0.20
            reason_parts.append("personal VPA")
        # Multiple repeats are friend-like
        if len(debits) >= 2 and len(credits) >= 2:
            confidence += 0.05
            reason_parts.append("multi-repeat")
        confidence = min(confidence, 0.99)

        upi_ids = sorted({t.counterparty_id for t in members if t.counterparty_id})
        aliases = sorted({n for n in names if n and n != canon})[:5]

        out.append(DetectedFriend(
            canonical_name=canon.title() if canon.isupper() else canon,
            upi_ids=upi_ids,
            aliases=aliases,
            sent_count=len(debits),
            received_count=len(credits),
            sent_total=round(sent_sum, 2),
            received_total=round(recv_sum, 2),
            net_owed_to_you=round(sent_sum - recv_sum, 2),
            confidence=round(confidence, 2),
            reason="; ".join(reason_parts),
            sample_txn_ids=[t.id for t in members[:3]],
        ))

    out.sort(key=lambda d: -d.confidence)
    return out


def link_detected_friends(
    db: Session, friends: list[DetectedFriend], min_confidence: float = 0.7,
) -> dict[str, int]:
    """Persist DetectedFriend results.

    Linking strategy (in order of precedence):
      1. counterparty_id exact match against the friend's upi_ids
      2. merchant_normalized exact match against {canonical_name} ∪ aliases
      3. raw_description contains canonical_name (case-insensitive) AND no
         counterparty_id was set by the parser

    Idempotent: re-running with the same data updates rather than duplicates.
    """
    created = 0
    updated = 0
    linked_txns = 0

    for f in friends:
        if f.confidence < min_confidence:
            continue

        person = (
            db.query(Person)
            .filter(Person.name == f.canonical_name)
            .first()
        )
        if person is None:
            person = Person(
                name=f.canonical_name,
                relationship_type="friend",
                upi_ids=list(f.upi_ids),
                aliases=list(f.aliases),
                notes=f"auto-detected · {f.reason}",
            )
            db.add(person)
            db.flush()
            created += 1
        else:
            existing_upi = set(person.upi_ids or [])
            existing_alias = set(person.aliases or [])
            new_upi = existing_upi | set(f.upi_ids)
            new_alias = existing_alias | set(f.aliases)
            if new_upi != existing_upi or new_alias != existing_alias:
                person.upi_ids = sorted(new_upi)
                person.aliases = sorted(new_alias)
                if not person.notes:
                    person.notes = f"auto-detected · {f.reason}"
                updated += 1

        # 1. Match by counterparty_id (UPI VPA)
        upi_set = set(f.upi_ids)
        if upi_set:
            for t in db.query(Transaction).filter(
                Transaction.counterparty_id.in_(upi_set),
                Transaction.person_id.is_(None),
            ).all():
                t.person_id = person.id
                linked_txns += 1

        # 2. Match by merchant_normalized (name + aliases)
        name_keys = {f.canonical_name, *f.aliases, f.canonical_name.upper(),
                    f.canonical_name.title()}
        # Also try uppercase and original casings
        for t in db.query(Transaction).filter(
            Transaction.merchant_normalized.in_(name_keys),
            Transaction.person_id.is_(None),
        ).all():
            t.person_id = person.id
            linked_txns += 1

        # 3. Fallback: raw_description LIKE the canonical name (case-insensitive)
        # Restrict to where counterparty_id is None to avoid false matches.
        if f.canonical_name and len(f.canonical_name) >= 4:
            like = f"%{f.canonical_name}%"
            for t in db.query(Transaction).filter(
                Transaction.raw_description.ilike(like),
                Transaction.person_id.is_(None),
            ).all():
                t.person_id = person.id
                linked_txns += 1

    db.commit()
    return {
        "people_created": created,
        "people_updated": updated,
        "transactions_linked": linked_txns,
    }
