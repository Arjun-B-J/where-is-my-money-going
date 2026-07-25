"""Shared cleanup for payee strings.

Bank narrations are machine-generated and noisy: payment-gateway prefixes,
reference numbers, IFSC codes, truncated URLs. The same three transformations
were duplicated across every parser, so they live here.
"""
from __future__ import annotations

import re

# Payment-gateway, terminal and transfer-rail prefixes that say nothing about who
# was actually paid. "RAZ*SOMESHOP" is Razorpay settling on behalf of SOMESHOP;
# "NEFT-CR-" only says the money arrived by NEFT. Applied repeatedly because real
# narrations stack them: "ACH-D-EXAMPLE BROKING".
_PREFIX_NOISE = re.compile(
    r"^(?:POS|PYU\*|RAZ\*|IND\*|PAYU\*|CCD\*|UPI|GPAY|BBPS|NEFT|IMPS|RTGS|ACH|MMT"
    r"|CR|DR|C|D)\b[\s/*-]*",
    re.IGNORECASE,
)

# Trailing URL fragments: "MYNTRA.COM/ORDER" -> "MYNTRA"
_TAIL_URL = re.compile(r"(?://|\.COM|\.IN\b|\.CO\b).*$", re.IGNORECASE)

# Long digit runs: reference numbers, IFSC codes, UPI transaction ids.
_LONG_DIGITS = re.compile(r"\s*\b\d{6,}\b")

# A UPI virtual payment address, kept as the counterparty key because it is the
# most stable identifier a UPI transaction carries.
#
# Hyphens are deliberately excluded from the local part. Narrations are
# hyphen-delimited — `UPI-SOME PAYEE-payee@okbank-REF` — so allowing them makes
# the match run leftwards and swallow the whole string. A VPA that genuinely
# contains a hyphen will be captured from the first hyphen onwards, which is a
# better failure than capturing the entire narration.
_VPA = re.compile(r"([A-Za-z0-9][A-Za-z0-9._]{1,48}@[A-Za-z][A-Za-z0-9.]{1,20})\b")

MAX_MERCHANT_LEN = 60


def extract_vpa(text: str) -> str | None:
    """Return the first UPI VPA in `text`, if any."""
    m = _VPA.search(text)
    return m.group(1) if m else None


def normalize_merchant(description: str) -> str | None:
    """Reduce a raw narration to a comparable payee name.

    Returns None when nothing meaningful survives, which is a valid outcome —
    better an explicit "unknown payee" than a merchant named "0000000123456".
    """
    s = _VPA.sub("", description)
    s = _LONG_DIGITS.sub("", s)
    # Strip stacked prefixes: "ACH-D-EXAMPLE BROKING", "NEFT-CR-EMPLOYER".
    for _ in range(4):
        stripped = _PREFIX_NOISE.sub("", s, count=1)
        if stripped == s:
            break
        s = stripped
    s = _TAIL_URL.sub("", s)
    s = re.sub(r"[\s|/*-]{2,}", " ", s)
    s = s.strip(" -|/*.,")
    if len(s) < 2:
        return None
    return s[:MAX_MERCHANT_LEN].strip()


def split_upi_narration(description: str) -> tuple[str | None, str | None]:
    """Best-effort (payee name, VPA) from a UPI-style narration.

    Indian UPI narrations are hyphen-delimited with the payee in the second
    field, e.g. `UPI-SOME PERSON-someperson@bank-HDFC0001234-4837-PAYMENT`.
    The layout is a convention rather than a spec, so this is heuristic and
    returns None rather than guessing when the shape does not match.
    """
    vpa = extract_vpa(description)

    parts = [p.strip() for p in description.split("-")]
    if len(parts) >= 2 and parts[0].upper() in {"UPI", "GPAY", "IMPS", "NEFT", "RTGS"}:
        candidate = _LONG_DIGITS.sub("", parts[1]).strip()
        if 2 <= len(candidate) <= MAX_MERCHANT_LEN:
            return candidate, vpa

    return normalize_merchant(description), vpa
