"""Checking that every rupee figure in an answer came from the data.

The rule this enforces is the project's oldest one: no number shown to the user
may come from the model. The tools compute every figure, and the model's job is
to choose the query and phrase the result. So once the model has answered, each
rupee figure in its text is looked up among the numbers the tools actually
returned. A figure that is not there was produced by the model, whether by
arithmetic, by estimation or from nowhere, and the answer is flagged instead of
being trusted.

Matching is tolerant in exactly one way. A figure written to four or more
significant digits ("₹12,345") claims that precision, so it must land within ₹1
or 0.5% of a tool value. A figure written round ("₹1.2 lakh", "₹32,000") claims
only its rounding, so it may be up to 5% away. Both directions of error cost
something: too strict and every correctly rounded answer is flagged, which
teaches the reader to ignore the flag; too loose and an invented round number
passes.

What this cannot check is whether a real number is attached to the right thing.
A food total quoted as the transport total passes, because the figure exists.
The chat eval measures that; this module measures provenance.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# A number as people and models write it: Indian grouping (1,23,456), Western
# grouping (123,456) or none, with optional decimals. The lookahead rejects a
# match that stops in the middle of digits, which would read "1,2345" as 1,234.
_NUMBER = r"(?P<number>\d+(?:,\d{2,3})*(?:\.\d+)?)(?!\d)"

# Scale words and their abbreviations. A unit must end at a word boundary, so
# "₹500 lunch" is five hundred rupees and not five hundred lakh.
_UNIT = (
    r"(?:\s?(?P<unit>lakhs?|lacs?|crores?|cr|thousand|million|mn|billion|bn|k|l|m|b)"
    r"(?![a-z]))?"
)

_MULTIPLIERS: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

# Not preceded by a digit, a comma, or a digit and a point: the start of a
# number, not the middle of one. "Rs.5000" still counts, because the point there
# follows a letter.
_START = r"(?<![\d,])(?<!\d\.)"

# Rupees marked before the number ("₹1,200", "Rs. 1,200", "INR 1200") or after
# it ("1,200 rupees", "1200 INR"). "Rs" has to stand as its own word, or "Mrs 2"
# and "hours 5" would both be money.
_MARKED_BEFORE = re.compile(
    rf"(?:₹|(?<![a-z])(?:rs\.?|inr)(?![a-z]))\s*[-+]?\s*{_NUMBER}{_UNIT}", re.IGNORECASE
)
_MARKED_AFTER = re.compile(rf"{_START}{_NUMBER}{_UNIT}\s*(?:rupees?|inr)(?![a-z])", re.IGNORECASE)
_ANY_NUMBER = re.compile(rf"{_START}{_NUMBER}{_UNIT}", re.IGNORECASE)

# Four significant digits is where a figure stops looking rounded: "₹4,499" is
# a specific amount, "₹4,500" and "₹4.5k" are approximations.
PRECISE_DIGITS = 4
PRECISE_SHARE = 0.005
ROUNDED_SHARE = 0.05


@dataclass(frozen=True)
class Figure:
    """One rupee figure as it was written in an answer."""

    text: str
    value: float
    significant_digits: int

    @property
    def precise(self) -> bool:
        return self.significant_digits >= PRECISE_DIGITS

    def matches(self, value: float) -> bool:
        """Whether `value` could be the number this figure was written from.

        The ₹1 floor applies to rounded figures too: "₹5" is a fair rendering of
        5.40, and a percentage of a small number would reject it.
        """
        target = abs(value)
        share = PRECISE_SHARE if self.precise else ROUNDED_SHARE
        return abs(self.value - target) <= max(1.0, share * target)


@dataclass(frozen=True)
class Grounding:
    """The verdict on one answer."""

    grounded: bool
    figures: tuple[Figure, ...]
    # As written in the answer, in order, without repeats. Shown to the user and
    # quoted back to the model in the corrective round.
    ungrounded: list[str]


def _significant_digits(number: str) -> int:
    """Significant digits of a written number.

    Trailing zeros of a whole number do not count ("32,000" is two digits of
    information); trailing zeros after a decimal point do ("12,345.00" is seven).
    """
    digits = number.replace(",", "")
    if "." in digits:
        whole, _, fraction = digits.partition(".")
        return len((whole + fraction).lstrip("0"))
    return len(digits.strip("0"))


def _figure(match: re.Match[str]) -> Figure:
    number = match.group("number")
    unit = (match.group("unit") or "").lower()
    return Figure(
        text=match.group(0).strip(),
        value=float(number.replace(",", "")) * _MULTIPLIERS.get(unit, 1.0),
        significant_digits=_significant_digits(number),
    )


def extract_figures(text: str) -> list[Figure]:
    """Every figure in `text` that is marked as rupees, in reading order.

    A figure marked on both sides ("₹500 rupees") is found by both patterns and
    counted once, keyed on where its digits start.
    """
    found: dict[int, Figure] = {}
    for pattern in (_MARKED_BEFORE, _MARKED_AFTER):
        for match in pattern.finditer(text):
            found.setdefault(match.start("number"), _figure(match))
    return [found[start] for start in sorted(found)]


def numbers_in_text(text: str) -> list[float]:
    """Every number in `text`, whether or not it is marked as money.

    Used for the user's own messages, whose figures an answer may repeat, and by
    the chat eval to find an expected figure in an answer however it was written.
    """
    return [_figure(match).value for match in _ANY_NUMBER.finditer(text)]


def numbers_in_json(payload: Any) -> list[float]:
    """Every number in a tool result, however deeply nested.

    Only JSON numbers count. Digits inside strings are dates, names and
    reference codes, and a figure that matched one of those would be grounded in
    nothing.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    found: list[float] = []
    pending = [payload]
    while pending:
        item = pending.pop()
        if isinstance(item, bool):
            continue
        if isinstance(item, int | float):
            found.append(float(item))
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return found


def check(answer: str, known: Iterable[float]) -> Grounding:
    """Find the rupee figures in `answer` that match none of `known`.

    `known` is every number the tools returned, plus any the user wrote in the
    question: repeating the user's own figure back is not the model inventing
    one. An answer with no rupee figures is grounded, since it claims nothing.
    """
    values = [abs(value) for value in known]
    figures = extract_figures(answer)
    missing: list[str] = []
    for figure in figures:
        if figure.text in missing:
            continue
        if not any(figure.matches(value) for value in values):
            missing.append(figure.text)
    return Grounding(grounded=not missing, figures=tuple(figures), ungrounded=missing)
