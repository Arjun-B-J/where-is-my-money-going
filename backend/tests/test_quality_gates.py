"""The gates that decide whether generated text is usable.

`looks_degenerate` replaced a check that counted repeated 4-character windows and
rejected anything appearing 8+ times. Every English paragraph trips that on
`" the"` and `"tion"`, so it rejected all valid prose — and because a fallback
always fired, nothing looked broken. The report simply never used the model.

The first test here is the one that would have caught it.
"""
from __future__ import annotations

import pytest

from app.pipeline.validator import looks_degenerate
from app.reports.narrative import _acceptable

GOOD_PROSE = (
    "You managed a healthy surplus this period, as your total credits of "
    "Rs 11,10,000 significantly outpaced your total debits of Rs 8,42,000. "
    "However, your spending lacks clarity because Rs 3,20,000 remains "
    "uncategorized. You should investigate these large, unidentified "
    "transactions to better understand the shape of your cash flow. "
    "Your fixed obligations remain substantial, with rent accounting for "
    "Rs 1,92,000 of the total outflow across the period."
)


def test_normal_prose_is_accepted():
    """The regression test. This paragraph repeats " the" and "tion" many times."""
    assert not looks_degenerate(GOOD_PROSE, max_words=600)
    assert _acceptable(GOOD_PROSE)


@pytest.mark.parametrize("text", [
    # The exact shape that reached the People page in the shipped version.
    "The account holder is account holder is account holder is account holder is "
    "account holder is account holder is account holder.",
    "ness ness ness ness ness ness ness ness ness ness.",
    "You spent money. You spent money. You spent money. You spent money. "
    "You spent money. You spent money.",
])
def test_degenerate_text_is_rejected(text):
    assert looks_degenerate(text)


def test_empty_text_is_rejected():
    assert looks_degenerate("")


def test_runaway_length_is_rejected():
    assert looks_degenerate("word " * 500, max_words=60)


def test_truncated_generation_is_rejected():
    """Constrained decoding can cut a string mid-sentence; that is not usable."""
    assert not _acceptable("You spent a great deal on food and also on")


def test_too_short_is_rejected():
    assert not _acceptable("You spent money.")


def test_prose_with_ordinary_repetition_survives():
    """Repeating a payee's name across sentences is normal writing, not a loop."""
    text = (
        "Rent is your largest fixed cost at Rs 32,000 a month. Rent has not "
        "changed across the period. Beyond rent, your spending is dominated by "
        "food delivery and cab rides, which together come to Rs 74,000."
    )
    assert not looks_degenerate(text)
    assert _acceptable(text)
