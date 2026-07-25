"""PDF report generation.

`theme` holds the palette and styles, `charts` renders images, `labels` maps
sources and payees to display text, `narrative` writes the prose, and
`spend_analysis` assembles the document.
"""
from app.reports.spend_analysis import render_spend_analysis

__all__ = ["render_spend_analysis"]
