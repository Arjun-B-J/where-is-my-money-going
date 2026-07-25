# Extending this to your bank

The demo runs on synthetic data. This document is how you replace it with your
own statements, and how you teach the app a format it has never seen.

It is written in the order you will actually need it:

1. [The one contract that matters](#1-the-one-contract-that-matters)
2. [Swapping synthetic data for your own](#2-swapping-synthetic-data-for-your-own)
3. [Mapping your accounts onto sources](#3-mapping-your-accounts-onto-sources)
4. [Adding a parser for a new bank](#4-adding-a-parser-for-a-new-bank)
5. [Teaching it about your money without writing code](#5-teaching-it-about-your-money-without-writing-code)
6. [Adding a category](#6-adding-a-category)
7. [Adding a detector](#7-adding-a-detector)
8. [Swapping the model](#8-swapping-the-model)
9. [Rules that should not be broken](#9-rules-that-should-not-be-broken)
10. [What I would build next, and why](#10-what-i-would-build-next-and-why)

---

## 1. The one contract that matters

Everything that produces transactions — a bank PDF parser, a CSV adapter, the
synthetic generator, the receipt scanner — emits the same object:

```python
# backend/app/ingest/records.py
@dataclass
class ParsedTxn:
    posted_at: datetime
    amount: float                 # always positive
    direction: TxnDirection       # DEBIT or CREDIT
    source: TxnSource             # which account
    raw_description: str
    merchant_normalized: str | None = None
    counterparty_id: str | None = None   # UPI VPA, when the statement has one
    balance_after: float | None = None
    extra_metadata: dict = field(default_factory=dict)
    external_id: str = ""         # derived if you leave it blank
```

Nothing downstream — categorising, detectors, analytics, report — knows or cares
where a row came from. That is the whole extension story: **if you can produce
`ParsedTxn` objects, the rest of the app already works.**

Two invariants the dataclass enforces for you:

- **`amount` is positive.** Sign lives in `direction`. Passing a negative amount
  raises immediately, because a negative amount means a parser leaked its
  statement's sign convention into the data model, and that bug is much cheaper
  to catch at the parser than three layers later in a chart.
- **`external_id` is a hash of `(source, date, amount, description)`.** Leave it
  blank and it is derived. This is what makes re-ingestion idempotent — drop the
  same file in twice, or two statements whose date ranges overlap, and nothing is
  double-counted.

---

## 2. Swapping synthetic data for your own

There is no "demo mode" flag to turn off. Synthetic and real data flow through
the same pipeline; they differ only in which branch produces the records.

```
                    ┌─ generate ──┐            (mode="demo")
seed ── branch ─────┤             ├── store ── rule_tag ── llm_tag ── agents ── finalize
                    └─ load_files ┘            (mode="files")
```

Three ways in, all landing in the same place:

```bash
# A directory of statements. Runs the full graph.
wimmg ingest ./statements

# A single file.
wimmg ingest ./statements/Acct_Statement_Jan.pdf

# Or drag files onto the Statements page in the UI, which calls POST /ingest/file.
```

To stop using the synthetic data entirely:

```bash
wimmg reset          # deletes transactions and run history
wimmg ingest ./statements
```

`reset` deliberately keeps your categories, rules and payee notes, because those
are configuration you built up, not data. `wimmg reset --all` drops those too and
asks first.

**Where to put statements.** Anywhere gitignored. `statements/`, `documents/` and
`data/` at the repository root are already ignored, along with every `*.pdf`,
`*.csv`, `*.xlsx` and `*.db` anywhere in the tree. See
[PRIVACY.md](PRIVACY.md) before you put real files in the repository directory at
all — a separate folder outside the repo is safer, and the CLI takes any path.

---

## 3. Mapping your accounts onto sources

`TxnSource` is deliberately generic:

```python
BANK            # savings / current account
BANK_SECONDARY  # a second bank account
CARD            # credit card
CARD_SECONDARY  # a second credit card
UPI             # UPI app export
WALLET          # prepaid wallet
RECEIPT         # scanned receipt
OTHER
```

It says `bank`, not `hdfc`, on purpose. An earlier version enumerated the actual
banks I use, which meant the schema itself recorded where I bank — and every row
of every user's database would have carried the same assumption.

Each parser has a default source, and you override it when an account does not
match that default:

```bash
wimmg ingest ./statements/hdfc-savings.pdf                          # -> BANK
wimmg ingest ./statements/icici-savings.pdf --source bank_2         # -> BANK_SECONDARY
wimmg ingest ./statements/amex.csv          --source card_2         # -> CARD_SECONDARY
```

Display names live in one dictionary, `SOURCE_LABELS` in
`backend/app/reports/labels.py`. If "Second bank account" is not what you want to
read in your report, that is the line to change:

```python
SOURCE_LABELS = {
    TxnSource.BANK.value: "Salary account",
    TxnSource.BANK_SECONDARY.value: "Joint account",
    TxnSource.CARD.value: "Travel card",
    ...
}
```

**If you have more than two banks or cards,** add enum members to `TxnSource` in
`models.py` and labels for them. Nothing else needs to change — the enum is
referenced by name everywhere, and `source.value.startswith("card")` is how the
report groups cards, so `card_3` is picked up automatically.

---

## 4. Adding a parser for a new bank

This is the most useful contribution anyone can make, and it is about 80 lines.

### Step 1 — look at what the PDF actually extracts to

Do this first. Bank PDFs rarely look like they read.

```python
import pdfplumber
with pdfplumber.open("statement.pdf") as pdf:
    print(pdf.pages[0].extract_text())
```

You are looking for two things: a line shape you can match with a regex, and
whether the withdrawal and deposit columns survive extraction. Usually they do
not — text extraction flattens the grid, and both amounts end up in one
whitespace-separated run. That is the reason for the next question.

### Step 2 — decide how you know a debit from a credit

In rough order of how much you should trust it:

1. **A closing-balance column.** Diff it against the previous row. This is what
   `hdfc_account_pdf.py` does, and it is the most reliable signal in the file
   because the bank's own arithmetic has to be right.
2. **An explicit marker.** Card statements often print `+` for refunds and
   credits — `hdfc_credit_pdf.py` matches on that.
3. **A signed amount.** CSV exports usually give you one; `hdfc_credit_csv.py`
   uses the sign and then takes the absolute value.
4. **Keywords in the narration.** Last resort only. `hdfc_account_pdf.py` uses it
   for the very first row, where there is no previous balance to diff against,
   and nowhere else.

### Step 3 — write the module

```python
# backend/app/ingest/parsers/yourbank_account_pdf.py
"""Parser for YourBank account statements.

<Describe the line format here, and say which signal gives you direction and
why. The next person to touch this — including you in six months — needs that
sentence more than they need the regex.>
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from app.ingest.normalize import split_upi_narration
from app.ingest.records import ParsedTxn
from app.models import TxnDirection, TxnSource

logger = logging.getLogger(__name__)

_TXN = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<description>.+?)\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s+"
    r"(?P<balance>[\d,]+\.\d{2})$"
)


def parse(path: str | Path, source: TxnSource = TxnSource.BANK) -> list[ParsedTxn]:
    txns: list[ParsedTxn] = []
    previous_balance: float | None = None

    with pdfplumber.open(Path(path)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                match = _TXN.match(line.strip())
                if not match:
                    continue

                amount = float(match["amount"].replace(",", ""))
                balance = float(match["balance"].replace(",", ""))

                # Direction from the balance movement — see Step 2.
                if previous_balance is None:
                    direction = TxnDirection.DEBIT
                else:
                    direction = (
                        TxnDirection.CREDIT
                        if balance > previous_balance
                        else TxnDirection.DEBIT
                    )
                previous_balance = balance

                description = match["description"].strip()
                merchant, vpa = split_upi_narration(description)

                txns.append(ParsedTxn(
                    posted_at=datetime.strptime(match["date"], "%d-%m-%Y").replace(hour=12),
                    amount=amount,
                    direction=direction,
                    source=source,
                    raw_description=description,
                    merchant_normalized=merchant,
                    counterparty_id=vpa,
                    balance_after=balance,
                    extra_metadata={"parser": "yourbank_account_pdf"},
                ))

    logger.info("yourbank_account_pdf: extracted %d transactions", len(txns))
    return txns
```

Reuse `app.ingest.normalize` rather than writing your own cleanup —
`split_upi_narration`, `normalize_merchant` and `extract_vpa` already handle
gateway prefixes, reference numbers, truncated URLs and VPA extraction, and every
parser sharing them means a fix helps all of them.

### Step 4 — register it

```python
# backend/app/ingest/parsers/__init__.py
Parser(
    name="yourbank_account_pdf",
    label="YourBank savings account (PDF statement)",
    extensions=(".pdf",),
    # Something that appears in this bank's statements and nobody else's.
    signature=re.compile(r"YourBank|Statement of Account", re.IGNORECASE),
    default_source=TxnSource.BANK,
    parse=yourbank_account_pdf.parse,
),
```

Order matters: the registry returns the **first** parser whose extension matches
and whose signature is found in the document's first page. Put more specific
signatures above more general ones.

**Signatures are matched against content, never filenames.** The first version of
this app picked parsers by looking for substrings like `"billedstatement"` in the
filename, which worked only for my own download naming and silently fell through
to the wrong parser for anyone else. If your signature needs the filename to
disambiguate, the signature is not specific enough yet.

### Step 5 — test it without committing a statement

You cannot commit a real statement, so build a fixture in the test.

For CSV that is trivial — see `test_parses_card_csv` in `tests/test_ingest.py`.
For PDF, either generate one with `reportlab` in the test, or extract the text
once and test your regex against that string directly. The parsing logic worth
testing is the regex and the direction inference, not `pdfplumber`.

```python
def test_direction_comes_from_the_balance():
    """A credit is recognised even when the narration looks like a payment."""
    rows = _parse_lines([
        "01-04-2026  OPENING                    0.00      50,000.00",
        "02-04-2026  SOME PAYMENT           1,000.00      51,000.00",  # balance rose
    ])
    assert rows[-1].direction == TxnDirection.CREDIT
```

Then confirm end to end against your own file, outside the repo:

```bash
wimmg ingest ~/statements/yourbank-jan.pdf -v
wimmg patterns    # individuals are shown as initials, safe to screenshot
```

### Step 6 — sanity-check the numbers

A parser that runs is not a parser that is right. Two checks catch most mistakes:

- Compare `wimmg status` totals against the closing balance printed on the
  statement's last page.
- Look at `/cross-source/card-reconcile`. If you have both a bank statement and
  the card statement for a bill you paid from it, the two sides should agree. A
  large mismatch usually means a parser bug, not a missed payment.

---

## 5. Teaching it about your money without writing code

Two things are user data, not code, precisely so you never have to fork the
project to record a fact about your own life.

**Tagging rules** live in the `rules` table — regex, optional amount bounds,
optional direction and source, a category, and a priority where lower wins.
`backend/app/seed.py` ships generic ones (national merchants, plus structural
patterns like `ATM-WDL` and BBPS bills that hold for any Indian statement). Add
your own:

```sql
INSERT INTO rules (name, pattern, direction, category, priority, enabled)
VALUES ('My landlord', '(?i)LANDLORD NAME', 'DEBIT', 'rent', 5, 1);
```

**Payee notes** populate the "Note" column of the report, via
`PUT /merchant-notes`:

```bash
curl -X PUT localhost:8000/merchant-notes \
  -H 'Content-Type: application/json' \
  -d '{"pattern": "SOME PERSON", "note": "Rent — recurring monthly", "priority": 10}'
```

This table exists so that the report generator never has to know anything about
you. The tempting alternative is a chain of `if` statements mapping payee names to
notes, and it is wrong twice over: private facts end up in shipped source, and the
notes are useless to every other user. **Anything that is a fact about your life
belongs in your database.** The privacy test suite enforces that the seeded
defaults stay structural.

---

## 6. Adding a category

The taxonomy is defined once, in `backend/app/llm/prompts.py`:

```python
CATEGORIES: list[tuple[str, str]] = [
    ("food", "eating out, food delivery, coffee, office cafeteria"),
    ...
    ("education", "courses, tuition, books, certifications"),   # your addition
]
```

That single list becomes the human-readable list inside the prompt, the `enum`
in the JSON Schema that constrains decoding, and the `Category` rows created by
`seed.py`. Add a colour and icon in `_CATEGORY_STYLE` in `seed.py`, then:

```bash
wimmg reset && wimmg ingest ./statements   # or just `wimmg tag` for untagged rows
```

`tests/test_rules.py::test_seeded_categories_match_the_model_taxonomy` fails if
the database and the prompt ever drift apart, which is the failure mode that
otherwise produces a model confidently returning a category your UI cannot render.

---

## 7. Adding a detector

Detectors are plain functions over the database — no model, no state:

```python
# backend/app/services/your_detector.py
@dataclass
class YourFinding:
    title: str
    detail: str
    amount: float
    txn_ids: list[int]

def detect_your_thing(db: Session) -> list[YourFinding]:
    ...
```

Wire it into a route in `backend/app/routes/patterns.py`, and into the report by
adding a section in `backend/app/reports/spend_analysis.py`. Keep them
deterministic: the reason findings can be shown to a user as "worth checking" is
that each one can be traced to specific rows.

---

## 8. Swapping the model

Change one environment variable:

```bash
LLM_MODEL=qwen3.5:14b
```

Before trusting it, run the probe that found the original bug:

```python
# Does it return valid, on-taxonomy JSON under a schema?
result = await get_llm().structured(messages, schema=TAGGING_SCHEMA)
print(result.ok, result.json())
```

Two things to check for any replacement:

- **Does it emit a separate thinking channel?** If so, keep `LLM_THINK=false`.
  With thinking enabled, `gemma4:26b` produced invalid JSON mid-string and took
  minutes per paragraph. If your model needs thinking to be any good at
  classification, set `LLM_THINK=true` and re-verify the JSON is still valid.
- **Is its confidence calibrated?** Feed it something genuinely opaque — a
  transfer to an unrecognised individual. If it answers 0.95, its confidence is
  decoration and the review-queue threshold in
  `CONFIDENCE_THRESHOLD` is meaningless. Ours reports 0.3–0.4 on those, which is
  what makes the queue worth reading.

A smaller model is a reasonable trade for the tagging pass, which is the slow
part — the report's prose is a handful of calls and can stay on the larger one via
the `model=` argument on `complete()`.

---

## 9. Rules that should not be broken

These are not style preferences. Each one is here because breaking it caused a
real bug in this project.

**Never let the model produce a number.** Amounts, dates and balances come from
deterministic code. The model classifies rows that already exist. There is no
prompt good enough to make a hallucinated rupee figure acceptable in a finance
tool.

**Never return fallback content that looks like a real answer.** `LLMClient`
returns `ok=False` with empty text on failure, and callers write no tag at all.
The version that returned `{"category": "uncategorized", "confidence": 0.0}`
instead put 285 unclassified rows into the database labelled as model output.

**Never hardcode a person into source code.** Not a landlord, not an employer's
payroll descriptor, not a family member. Rules and payee notes exist for this.
`tests/test_privacy.py` will fail the build.

**Never commit a statement, a database, or a screenshot of real data.** The
gitignore covers the obvious extensions and CI checks for them, but a screenshot
is an image file that no extension filter catches. Regenerate screenshots from
`wimmg demo`, whose payees are famous footballers precisely so that nobody can
mistake a demo screenshot for a real ledger.

**Do not "clean up" the emoji in the demo data.** One payee is named
`CRISTIANO RONALDO 🐐`, and that character is not in the report's font. It is
there so that every demo report exercises `reports.theme.pdf_safe` and the friend
detector's name normalisation — a real payee name can contain anything a bank let
through, and reportlab renders a missing glyph as a black rectangle.

**A quality gate must be tested against known-good input.** The prose gate
rejected every valid paragraph for weeks and nobody noticed, because the fallback
was decent. If you add a gate, the first test asserts that good output passes.

---

## 10. What I would build next, and why

Ordered by how much the problem actually bothers me, which is not the same as how
interesting the engineering is.

**Rule editing in the UI.** Right now teaching the app about a recurring payee
means an SQL statement or a curl. That is the single biggest gap between this
being a tool I use and a tool anyone else could. The review queue already knows
which payees recur — the missing piece is "always call this rent", one click, from
the row you are already looking at.

**Learning from the review queue.** Correcting a category today teaches the system
nothing: tag the same opaque payee fifty times and the fifty-first still arrives
`uncategorized` at 0.35 confidence. The fix is retrieval over the user's *own*
confirmed tags — embed descriptions, and before classifying a new row, pull the
nearest rows where `tag_source = 'user'` into the prompt as examples. A small
embedding model in Ollama, vectors in the same SQLite file, nothing leaving the
machine. It turns review effort into signal, and it is the one place in this
architecture where retrieval genuinely earns its keep rather than replacing an
exact aggregate with an approximate one. Reasoning in
[DECISIONS.md §15](DECISIONS.md#15-no-vector-search-and-where-it-would-actually-earn-its-place).

**Ingest on file drop.** Every month I export three statements and run one
command. Watching a folder and ingesting incrementally removes the only recurring
manual step. The idempotent `external_id` already makes this safe; it needs a
watcher and nothing else.

**A second bank's parsers.** Three parsers, one bank, is the honest limit of this
project today. SBI, ICICI and Axis cover most of India between them. This is the
work that turns a personal tool into something with users, and it is
deliberately the easiest contribution to make.

**Confidence calibration measured, not assumed.** The threshold of 0.70 is a
judgement call. With a few hundred human-reviewed rows there is enough data to
check whether the model's 0.8 actually means 80%, and to move the threshold to
where it belongs. That turns the review queue from a heuristic into something
measured.

**A "what changed" view.** The report answers "where did it go". The question I
actually ask each month is "what is different from last month" — which
subscription appeared, which category moved, what stopped. The monthly data is
already there; it is a diff and a page.

Deliberately not on this list: multi-user accounts, cloud sync, and a mobile app.
All three require the data to leave the machine, and that constraint is the reason
this project exists.
