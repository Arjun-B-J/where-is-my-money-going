"""Rupee formatting.

Its own module because several layers need it — CLI output, chat prompts, PDF
charts — and importing it from the report theme would drag matplotlib into a
chat request.

Indian numbering groups by lakh and crore, not by thousand and million: readers
expect "₹1.20L", not "₹120K".
"""
from __future__ import annotations

LAKH = 100_000
CRORE = 100 * LAKH


def rupees(amount: float, *, compact: bool = False) -> str:
    """Format an amount in rupees.

    `compact=True` abbreviates to lakh/crore for chart labels and KPI tiles where
    space is tight. Tables and prose use the full figure so they stay auditable.
    """
    if not compact:
        return f"₹{amount:,.0f}"
    magnitude = abs(amount)
    if magnitude >= CRORE:
        return f"₹{amount / CRORE:.2f}Cr"
    if magnitude >= LAKH:
        return f"₹{amount / LAKH:.2f}L"
    if magnitude >= 1_000:
        return f"₹{amount / 1_000:.1f}k"
    return f"₹{amount:,.0f}"


def signed_rupees(amount: float, *, compact: bool = False) -> str:
    """As `rupees`, with an explicit + on positive values."""
    return ("+" if amount > 0 else "") + rupees(amount, compact=compact)


def percent(part: float, whole: float) -> str:
    """A share as a percentage, or an em dash when the denominator is zero."""
    return f"{part / whole * 100:.1f}%" if whole else "—"
