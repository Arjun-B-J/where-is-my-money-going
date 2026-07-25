"""Guards against personal data reaching the repository.

A statement carries other people's names and phone numbers, not just your own, so
"nothing personal in the repo" needs enforcing rather than intending. These tests
fail CI rather than relying on anyone noticing during review. See docs/PRIVACY.md
for the threat model.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.reports.labels import looks_like_individual, redact_payee
from app.seed import DEFAULT_MERCHANT_NOTES, DEFAULT_RULES

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "app"

# An Indian mobile number: 10 digits starting 6-9. The synthetic dataset uses a
# deliberately unassignable 90000 000xx pattern, which is allowed.
_PHONE = re.compile(r"\b[6-9]\d{9}\b")
_ALLOWED_FAKE_PHONE = re.compile(r"\b9000000\d{3}\b")

# A real UPI address, as opposed to the @okbank placeholder used in demo data.
_REAL_VPA = re.compile(
    r"\b[\w.-]+@(?:oksbi|okicici|okaxis|okhdfcbank|ybl|paytm|ibl|axl|apl)\b",
    re.IGNORECASE,
)


def _source_files() -> list[Path]:
    return [p for p in SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_phone_numbers_in_source():
    offenders: list[str] = []
    for path in _source_files():
        for match in _PHONE.finditer(path.read_text(encoding="utf-8")):
            if not _ALLOWED_FAKE_PHONE.fullmatch(match.group()):
                offenders.append(f"{path.name}: {match.group()}")
    assert not offenders, f"possible real phone numbers in source: {offenders}"


def test_no_real_upi_handles_in_source():
    offenders = [
        f"{path.name}: {match.group()}"
        for path in _source_files()
        for match in _REAL_VPA.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"real-looking UPI addresses in source: {offenders}"


def test_no_employer_or_bank_identifiers_in_source():
    """A payroll descriptor or an employer name identifies exactly one user.

    Salary is found by category instead, which works for everyone.
    """
    forbidden = ("wfispl", "wells fargo", "embassy tech")
    offenders: list[str] = []
    for path in _source_files():
        lowered = path.read_text(encoding="utf-8").lower()
        offenders += [f"{path.name}: {word}" for word in forbidden if word in lowered]
    assert not offenders, f"employer identifiers in source: {offenders}"


def test_seed_data_names_no_individuals():
    """Seeded rules must not encode who a particular user pays.

    A rule naming an individual is useful to one person and shipped to everyone.
    """
    for rule in DEFAULT_RULES:
        assert rule.get("person_name") is None, (
            f"rule {rule['name']!r} names a person; people are detected from data, "
            "not seeded"
        )


def test_seeded_notes_are_structural_only():
    """Every default note matches a transaction type, not a particular payee.

    Explicit allowlist rather than a heuristic: adding a note for your own
    landlord is a perfectly good thing to do in *your* database, and this test
    exists to stop it being shipped to everyone else's.
    """
    allowed = {
        "SALARY", "EMPLOYER", "ATM", "CREDIT CARD PAYMENT", "CRED", "EMI", "RENT",
    }
    patterns = {note["pattern"] for note in DEFAULT_MERCHANT_NOTES}
    assert patterns <= allowed, f"unexpected seeded note patterns: {patterns - allowed}"


# ---- redaction --------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("SOME PERSON NAME", "S. P. N."),
    ("LIONEL MESSI", "L. M."),
])
def test_individuals_are_reduced_to_initials(name, expected):
    assert redact_payee(name) == expected


@pytest.mark.parametrize("name", [
    "EXAMPLE SUPERMARKET LTD",
    "POS EXAMPLE COFFEE",
    "ATM WITHDRAWAL",
    "EXAMPLE TECHNOLOGIES",
])
def test_businesses_are_left_alone(name):
    assert redact_payee(name) == name


def test_individual_detection():
    assert looks_like_individual("LIONEL MESSI")
    assert not looks_like_individual("EXAMPLE MARKETPLACE PVT LTD")
    assert not looks_like_individual("POS SOMETHING")


# ---- the one committed data-shaped file --------------------------------------

SAMPLE_REPORT = Path(__file__).resolve().parents[2] / "docs" / "sample-report.pdf"


@pytest.mark.skipif(not SAMPLE_REPORT.exists(), reason="sample report not built")
def test_committed_sample_report_is_synthetic():
    """The sample report in docs/ must come from the synthetic dataset.

    It is the single exception to "no data files in the repository", so it needs
    its own check: rebuilding it against real statements and committing the result
    would put exactly what the rest of this file guards against into git.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        pytest.skip("pdfplumber not installed")

    with pdfplumber.open(SAMPLE_REPORT) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # The synthetic dataset's payees are all prefixed or named from a fixed set.
    assert "EXAMPLE" in text.upper(), (
        "sample-report.pdf does not look like it came from the demo dataset — "
        "rebuild it with `wimmg reset --all && wimmg demo && wimmg report`"
    )
    assert not _PHONE.search(text), "sample-report.pdf contains a phone number"
    assert not _REAL_VPA.search(text), "sample-report.pdf contains a real UPI handle"


# ---- text that the report font cannot draw ------------------------------------


def test_pdf_safe_drops_undrawable_characters():
    """A payee name can contain anything a bank let through.

    reportlab does not fall back to another font: a missing glyph renders as a
    black rectangle. The demo dataset ships a name with an emoji specifically so
    this path runs on every demo report.
    """
    from app.reports.theme import pdf_safe

    assert pdf_safe("CRISTIANO RONALDO 🐐") == "CRISTIANO RONALDO"
    # Characters the font does have are untouched, including the rupee sign.
    assert pdf_safe("Rent ₹32,000 — paid") == "Rent ₹32,000 — paid"


def test_person_name_detection_tolerates_decoration():
    """Stripping decoration must not disqualify someone from the People view."""
    from app.services.friend_detector import _looks_like_person_name

    assert _looks_like_person_name("CRISTIANO RONALDO 🐐")
    assert _looks_like_person_name("LIONEL MESSI")
    assert not _looks_like_person_name("EXAMPLE MARKETPLACE PVT LTD")
