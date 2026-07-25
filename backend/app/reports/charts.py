"""Chart rendering for the PDF report.

Each function returns PNG bytes so the caller can drop it straight into a
reportlab `Image`. Matplotlib is forced onto the non-interactive Agg backend at
import time because this runs inside a web request with no display.
"""
from __future__ import annotations

import io
from typing import Literal

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from app.reports.theme import (
    CATEGORY_COLORS,
    MPL_AMBER,
    MPL_AMBER_SOFT,
    MPL_AXIS,
    MPL_FAINT,
    MPL_INK,
    MPL_RULE,
    axis_formatter,
    rupees,
)

_LABEL_MAX = 34


def _render(fig: plt.Figure) -> bytes:
    """Serialise and always close the figure — leaked figures exhaust memory."""
    try:
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=160, bbox_inches="tight", facecolor="white")
        return buffer.getvalue()
    finally:
        plt.close(fig)


def _empty(message: str = "No data for this section") -> bytes:
    fig, ax = plt.subplots(figsize=(8, 1.8))
    ax.text(0.5, 0.5, message, ha="center", va="center", color=MPL_FAINT, fontsize=10)
    ax.axis("off")
    return _render(fig)


def _clean_axes(ax: plt.Axes, *, grid_axis: Literal["both", "x", "y"]) -> None:
    ax.grid(axis=grid_axis, color=MPL_RULE, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(MPL_RULE)


def horizontal_bars(
    data: list[tuple[str, float]], title: str, *, color: str = MPL_INK
) -> bytes:
    """Ranked horizontal bars with value labels. Used for categories and payees."""
    if not data:
        return _empty()

    labels = [name[:_LABEL_MAX] for name, _ in data]
    values = [value for _, value in data]
    height = max(2.4, 0.42 * len(data) + 0.7)

    fig, ax = plt.subplots(figsize=(8.5, height))
    # Reversed so the largest value sits at the top of the chart.
    bars = ax.barh(labels[::-1], values[::-1], color=color, height=0.62)
    largest = max(values)

    for bar, value in zip(bars, values[::-1], strict=True):
        ax.text(
            value + largest * 0.015,
            bar.get_y() + bar.get_height() / 2,
            rupees(value, compact=True),
            va="center", fontsize=8.5, color=MPL_INK,
        )

    ax.set_xlim(0, largest * 1.16)
    ax.set_title(title, fontsize=10.5, color=MPL_INK, pad=8)
    ax.xaxis.set_major_formatter(FuncFormatter(axis_formatter))
    ax.tick_params(axis="x", labelsize=8, colors=MPL_AXIS)
    ax.tick_params(axis="y", labelsize=9, colors=MPL_INK)
    _clean_axes(ax, grid_axis="x")
    return _render(fig)


def monthly_flow(
    months: list[str], money_in: list[float], money_out: list[float]
) -> bytes:
    """Grouped bars comparing money in against money out, month by month."""
    if not months:
        return _empty()

    positions = range(len(months))
    width = 0.38

    fig, ax = plt.subplots(figsize=(8.5, 3.4))
    ax.bar([p - width / 2 for p in positions], money_in, width,
           label="In", color=MPL_INK)
    ax.bar([p + width / 2 for p in positions], money_out, width,
           label="Out", color=MPL_AMBER)

    ax.set_xticks(list(positions))
    ax.set_xticklabels(months, fontsize=8, color=MPL_AXIS)
    ax.yaxis.set_major_formatter(FuncFormatter(axis_formatter))
    ax.tick_params(axis="y", labelsize=8, colors=MPL_AXIS)
    ax.set_title("Money in vs money out, by month", fontsize=10.5, color=MPL_INK, pad=8)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right", ncols=2)
    _clean_axes(ax, grid_axis="y")
    return _render(fig)


def outflow_trail(points: list[tuple[str, float]]) -> bytes:
    """Line-and-area chart of total spend per month."""
    if not points:
        return _empty()

    labels = [label for label, _ in points]
    values = [value for _, value in points]

    fig, ax = plt.subplots(figsize=(8.5, 3.0))
    ax.fill_between(labels, values, color=MPL_AMBER_SOFT, alpha=0.25)
    ax.plot(labels, values, color=MPL_AMBER, linewidth=2,
            marker="o", markersize=4, markerfacecolor=MPL_AMBER)

    # Anchor at zero so the eye reads bar height as magnitude, not as noise
    # around an arbitrary baseline.
    ax.set_ylim(0, max(values) * 1.15 if any(values) else 1)
    ax.set_title("Total spend per month", fontsize=10.5, color=MPL_INK, pad=8)
    ax.yaxis.set_major_formatter(FuncFormatter(axis_formatter))
    ax.tick_params(axis="x", labelsize=8, colors=MPL_AXIS, rotation=45)
    ax.tick_params(axis="y", labelsize=8, colors=MPL_AXIS)
    _clean_axes(ax, grid_axis="y")
    return _render(fig)


def category_donut(data: list[tuple[str, float]], title: str) -> bytes:
    """Donut with a side legend carrying amounts and shares.

    Labels sit in the legend rather than on the slices: small slices overlap
    and become unreadable when annotated in place.
    """
    if not data:
        return _empty()

    slices = data[:12]
    labels = [name for name, _ in slices]
    values = [value for _, value in slices]
    total = sum(values) or 1

    fig, (donut, legend) = plt.subplots(
        1, 2, figsize=(9, max(3.4, 0.34 * len(slices) + 1.0)),
        gridspec_kw={"width_ratios": [1, 1.35]},
    )
    donut.pie(
        values, colors=CATEGORY_COLORS[:len(values)], startangle=90, counterclock=False,
        wedgeprops={"edgecolor": "white", "linewidth": 2.2, "width": 0.42},
    )
    donut.set_title(title, fontsize=10.5, color=MPL_INK, pad=4)

    legend.axis("off")
    line_height = 1.0 / max(len(slices), 8)
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        y = 0.95 - index * line_height
        legend.add_patch(plt.Rectangle(
            (0, y - 0.016), 0.035, 0.032, transform=legend.transAxes,
            facecolor=CATEGORY_COLORS[index % len(CATEGORY_COLORS)],
            edgecolor="white", linewidth=1,
        ))
        legend.text(0.065, y, label[:28], transform=legend.transAxes,
                    fontsize=9, color=MPL_INK, va="center", fontweight="bold")
        legend.text(0.99, y, f"{rupees(value, compact=True)}  ·  {value / total * 100:.1f}%",
                    transform=legend.transAxes, fontsize=8.5, color=MPL_AXIS,
                    va="center", ha="right")
    return _render(fig)


def weekday_pattern(totals: dict[str, float]) -> bytes:
    """Spend by day of week, highlighting the heaviest day."""
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    values = [totals.get(day, 0.0) for day in order]
    if not any(values):
        return _empty()

    peak = max(range(len(values)), key=lambda i: values[i])
    colors_ = [MPL_AMBER if i == peak else MPL_INK for i in range(len(values))]

    fig, ax = plt.subplots(figsize=(8.5, 2.6))
    ax.bar(order, values, color=colors_, width=0.62)
    ax.set_title("Spend by day of week", fontsize=10.5, color=MPL_INK, pad=8)
    ax.yaxis.set_major_formatter(FuncFormatter(axis_formatter))
    ax.tick_params(axis="x", labelsize=9, colors=MPL_INK)
    ax.tick_params(axis="y", labelsize=8, colors=MPL_AXIS)
    _clean_axes(ax, grid_axis="y")
    return _render(fig)
