"""Colours, paragraph styles and number formatting for the PDF report.

Split out from the document builder so that the layout code reads as layout,
and so charts and tables cannot drift to slightly different colours.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Re-exported so chart and layout code has one import for everything visual.
from app.money import percent, rupees, signed_rupees

logger = logging.getLogger(__name__)

__all__ = [
    "axis_formatter", "body_font", "bold_font", "pdf_safe", "percent",
    "rupees", "signed_rupees", "styles",
]


@functools.lru_cache(maxsize=1)
def _fonts() -> tuple[str, str]:
    """Register a font that can render ₹, returning (regular, bold) names.

    PDF's built-in Helvetica predates the rupee sign and has no glyph for it, so
    every amount in the report rendered as a blank gap — in a document whose whole
    subject is rupee amounts.

    DejaVu Sans has the glyph and ships inside matplotlib, which is already a
    dependency for the charts, so this needs no new package and no font file in
    the repository. Falls back to Helvetica if it cannot be found, which keeps the
    report generating rather than failing over typography.
    """
    try:
        import matplotlib

        font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        regular, bold = font_dir / "DejaVuSans.ttf", font_dir / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("ReportSans", str(regular)))
            pdfmetrics.registerFont(TTFont("ReportSans-Bold", str(bold)))
            pdfmetrics.registerFontFamily(
                "ReportSans", normal="ReportSans", bold="ReportSans-Bold"
            )
            return "ReportSans", "ReportSans-Bold"
    except Exception as e:
        logger.warning("Could not register DejaVu Sans, falling back: %s", e)

    logger.warning("Using Helvetica: the rupee sign will not render in PDF text.")
    return "Helvetica", "Helvetica-Bold"


def body_font() -> str:
    return _fonts()[0]


def bold_font() -> str:
    return _fonts()[1]


@functools.lru_cache(maxsize=1)
def _drawable_codepoints() -> frozenset[int] | None:
    """Codepoints the registered font can actually draw, or None if unknown."""
    try:
        from reportlab.pdfbase.pdfmetrics import getFont

        face = getFont(body_font()).face
        return frozenset(face.charToGlyph)
    except Exception:
        return None


def pdf_safe(text: str, placeholder: str = "") -> str:
    """Drop characters the report font has no glyph for.

    A payee name comes from a bank statement, so it can contain anything —
    emoji, unusual scripts, box-drawing characters someone pasted into a UPI
    note. reportlab does not fall back to another font: a missing glyph is drawn
    as a black rectangle, or the build fails outright.

    Silently dropping the character is the right trade here. The alternative is a
    report that renders a payee as "PERSON ▯▯" or does not render at all, and the
    surrounding text carries the meaning either way.
    """
    drawable = _drawable_codepoints()
    if drawable is None:
        return text
    if all(ord(ch) in drawable for ch in text):
        return text
    cleaned = "".join(ch if ord(ch) in drawable else placeholder for ch in text)
    # Collapse whitespace left behind by removed characters.
    return " ".join(cleaned.split())

# ---- palette ----------------------------------------------------------------

INK = colors.HexColor("#0F172A")          # near-black, body text and cover
INK_SOFT = colors.HexColor("#1E293B")
MUTED = colors.HexColor("#64748B")
FAINT = colors.HexColor("#94A3B8")
RULE = colors.HexColor("#E2E8F0")

AMBER = colors.HexColor("#EA580C")        # accent
AMBER_DEEP = colors.HexColor("#9A3412")
AMBER_SOFT = colors.HexColor("#FED7AA")
AMBER_WASH = colors.HexColor("#FFF7ED")   # table row banding

POSITIVE = colors.HexColor("#15803D")
CAUTION = colors.HexColor("#B45309")
NEGATIVE = colors.HexColor("#B91C1C")

# Hex strings for matplotlib, which does not take reportlab colour objects.
MPL_INK = "#0F172A"
MPL_AMBER = "#EA580C"
MPL_AMBER_SOFT = "#FB923C"
MPL_RULE = "#E2E8F0"
MPL_AXIS = "#475569"
MPL_FAINT = "#94A3B8"

# Ordered so that adjacent slices in a donut stay distinguishable, including in
# greyscale print.
CATEGORY_COLORS = [
    "#0F172A", "#1D4ED8", "#0EA5E9", "#0D9488", "#16A34A",
    "#84CC16", "#F59E0B", "#EA580C", "#DC2626", "#9333EA",
    "#DB2777", "#6B7280",
]


# ---- number formatting ------------------------------------------------------


def axis_formatter(value: float, _position: int = 0) -> str:
    """Tick-label callback for matplotlib axes."""
    return rupees(value, compact=True)


# ---- paragraph styles -------------------------------------------------------


def styles() -> dict[str, ParagraphStyle]:
    """Every paragraph style the report uses, keyed by role."""
    base = getSampleStyleSheet()
    regular, bold = _fonts()

    def style(name: str, parent: str, **kwargs) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base[parent], **kwargs)

    return {
        # Cover, set on a dark background.
        "cover_eyebrow": style(
            "CoverEyebrow", "BodyText", fontSize=11, leading=14, spaceAfter=10,
            textColor=AMBER_SOFT, fontName=bold, alignment=TA_LEFT,
        ),
        "cover_title": style(
            "CoverTitle", "Heading1", fontSize=40, leading=46, spaceAfter=2,
            textColor=colors.white, fontName=bold,
        ),
        "cover_period": style(
            "CoverPeriod", "Heading1", fontSize=40, leading=46, spaceAfter=22,
            textColor=AMBER, fontName=bold,
        ),
        "cover_lede": style(
            "CoverLede", "BodyText", fontSize=13, leading=19, fontName=regular,
            textColor=colors.HexColor("#CBD5E1"),
        ),

        # Interior.
        "h1": style(
            "H1", "Heading1", fontSize=22, leading=27, spaceBefore=4, spaceAfter=10,
            textColor=INK, fontName=bold,
        ),
        "h2": style(
            "H2", "Heading2", fontSize=13, leading=17, spaceBefore=16, spaceAfter=4,
            textColor=AMBER, fontName=bold,
        ),
        "h3": style(
            "H3", "Heading3", fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=2,
            textColor=AMBER_DEEP, fontName=bold,
        ),
        "body": style(
            "Body", "BodyText", fontSize=10, leading=15, spaceAfter=7, fontName=regular,
            textColor=INK, alignment=TA_JUSTIFY,
        ),
        "caption": style(
            "Caption", "BodyText", fontSize=8.5, leading=12, spaceAfter=4, fontName=regular,
            textColor=MUTED,
        ),
        "notice": style(
            "Notice", "BodyText", fontSize=9.5, leading=14, spaceAfter=6,
            textColor=AMBER_DEEP, fontName=bold,
        ),

        # KPI tiles.
        "kpi_label": style(
            "KpiLabel", "BodyText", fontSize=8.5, leading=11, fontName=regular,
            textColor=MUTED, alignment=TA_CENTER,
        ),
        "kpi_value": style(
            "KpiValue", "BodyText", fontSize=17, leading=21,
            textColor=INK, alignment=TA_CENTER, fontName=bold,
        ),
        "kpi_value_accent": style(
            "KpiValueAccent", "BodyText", fontSize=17, leading=21,
            textColor=AMBER, alignment=TA_CENTER, fontName=bold,
        ),
        "kpi_value_positive": style(
            "KpiValuePos", "BodyText", fontSize=17, leading=21,
            textColor=POSITIVE, alignment=TA_CENTER, fontName=bold,
        ),
        "kpi_value_negative": style(
            "KpiValueNeg", "BodyText", fontSize=17, leading=21,
            textColor=NEGATIVE, alignment=TA_CENTER, fontName=bold,
        ),

        # Table cells need their own styles so long values wrap.
        "cell": style("Cell", "BodyText", fontSize=9, leading=12, textColor=INK, fontName=regular),
        "cell_right": style(
            "CellRight", "BodyText", fontSize=9, leading=12, textColor=INK,
            alignment=2, fontName=regular,
        ),
        "cell_head": style(
            "CellHead", "BodyText", fontSize=9, leading=12,
            textColor=colors.white, fontName=bold,
        ),
    }
