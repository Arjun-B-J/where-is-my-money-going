# Architecture

A tour of how a PDF becomes a categorised ledger. For the reasoning behind
individual choices see [DECISIONS.md](DECISIONS.md); for how to extend any of it
see [EXTENDING.md](EXTENDING.md).

---

## The shape of it

```
                      ┌──────────────────────────────────────────────┐
  statements ─────────▶│  ingest/     deterministic. no model runs.  │
  (PDF, CSV)          │  parsers → normalize → records → loader      │
                      └───────────────────┬──────────────────────────┘
                                          │  ParsedTxn
  demo/generator ─────────────────────────┤  (same contract)
  (synthetic)                             ▼
                      ┌──────────────────────────────────────────────┐
                      │  SQLite                                      │
                      │  transactions · people · rules · notes · runs│
                      └───────────────────┬──────────────────────────┘
                                          ▼
                      ┌──────────────────────────────────────────────┐
                      │  pipeline/   LangGraph                       │
                      │  rule_tag → llm_tag → friends → validator    │
                      └───────────────────┬──────────────────────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
              services/              reports/               routes/
         analytics · trends      theme · charts         one router
         detectors · budget      narrative · PDF        per domain
                    │                     │                     │
                    └─────────────────────┴──────────┬──────────┘
                                                     ▼
                                          Next.js frontend
                                        (proxied via /api/backend)
```

Two properties are worth calling out because everything else follows from them.

**The model sits in exactly one place.** `app/llm/` is the only package that
speaks to Ollama. Extraction never calls it, detectors never call it, and
analytics never calls it. Categorising and prose do, and both go through one
client with one failure contract.

**Every source produces the same record.** A bank PDF, a CSV export, the
synthetic generator and the receipt scanner all emit `ParsedTxn`. Nothing
downstream knows which produced a row.

---

## Layer by layer

### `app/ingest/` — extraction

No model runs in this package. That is the point.

| Module | Job |
|---|---|
| `records.py` | `ParsedTxn`, and the identity hash that makes re-ingestion a no-op |
| `normalize.py` | Payee-string cleanup shared by every parser: gateway prefixes, reference numbers, truncated URLs, VPA extraction |
| `parsers/__init__.py` | Registry; picks a parser by **sniffing document content**, not the filename |
| `parsers/*.py` | One module per format |
| `loader.py` | Batched dedupe and insert |

`loader.py` resolves duplicates with one query for the whole batch rather than one
per row — on a twelve-month multi-statement load that is roughly 2,000 queries
saved — and dedupes *within* the batch too, because overlapping statement date
ranges are the normal case.

### `app/llm/` — the model boundary

`client.py` has one rule: **a failed call never returns content that looks like
an answer.** Every method returns `LLMResult` with an explicit `ok` flag; on
failure `text` is empty. There is no third state and no placeholder.

`prompts.py` holds prompts and JSON schemas. The category taxonomy is defined
once, in `CATEGORIES`, and reused three ways: as the list inside the system
prompt, as the `enum` that constrains decoding, and by `seed.py` when creating
`Category` rows. A test fails if the database and the prompt ever drift apart.

### `app/pipeline/` — orchestration

```
seed ─┬─▶ generate ──▶ store ─┐
      └─▶ load_files ─────────┴─▶ rule_tag ─▶ llm_tag ─▶ friend_discover ─▶ validator ─▶ finalize
```

One conditional edge, after `seed`, choosing between synthetic data and real
files. Both converge on the same categorising path — which matters because they
used not to, and the two implementations had drifted.

Each node returns a partial `PipelineState` (a `TypedDict`) and records its own
timing. `llm_tag` commits in chunks of 50, so an interrupted run over a year of
statements keeps its progress.

`validator.py` is the second-opinion agent. It re-checks every tag below the
confidence threshold: agreement raises confidence, a confident disagreement
replaces the tag and records why, mutual uncertainty routes to a human. It also
classifies relationships, and sanity-checks that output before storing it —
`looks_degenerate()` lives here because model text going into the database is the
thing that needs guarding.

### `app/services/` — analysis

Plain functions over the database. No model, no shared state, no ordering
requirements between them.

`analytics` (dashboard aggregates) · `trends` (buckets and a linear forecast) ·
`patterns` (recurring, two-way flows, category outliers) ·
`friend_detector` (structural person detection) · `emi_detector` ·
`subscriptions` (cadence-based) · `anomaly_hunter` · `cross_source` ·
`budget` · `group_by_recipient` · `insights`

`subscriptions` runs *after* categorising on purpose: the category is a much
better signal for "is this a service I subscribe to" than any pattern match on the
payee, and it is what excludes rent and card bills from the subscription total.

### `app/reports/` — the PDF

| Module | Job |
|---|---|
| `theme.py` | Palette and paragraph styles |
| `charts.py` | Matplotlib figures, returned as PNG bytes |
| `labels.py` | Source labels, payee notes from the database, redaction |
| `narrative.py` | The three model passes, each with a `computed` fallback |
| `spend_analysis.py` | Document assembly |

Prose is requested as **plain text**, never as a string inside a JSON object:
constrained decoding cannot bound a free-text field's length, and a paragraph that
runs past its closing quote destroys the whole object. Only the closing
observations use a schema, where the structure is worth more than the headroom.

The render is async at the top (model passes run concurrently where independent)
and hands the blocking reportlab/matplotlib work to a worker thread, so a
sixty-second report does not stall the event loop.

### `app/routes/` — HTTP

One router per domain, registered in a single `ROUTERS` tuple in `main.py`. Two of
these used to be second routers exported from unrelated modules, which made the
URL map impossible to read off the source.

### `frontend/`

Next.js 15 App Router. `lib/api.ts` is a typed client mirroring `app/schemas.py`;
API calls go through a Next rewrite at `/api/backend/*`, so the browser only ever
talks to one origin — no CORS preflight, and no backend URL in the client bundle.

---

## Data model

```
transactions ──┬── person_id ──▶ people
               │
               └── category ────▶ categories (by name)

rules            regex → category, editable without a deploy
merchant_notes   payee pattern → the report's "Note" column
pipeline_runs    audit record per run, including llm_available and llm_failed
```

Three details carry weight:

**`amount` is always positive; direction is a separate column.** Sign conventions
differ between statement formats and must not leak into the data. `ParsedTxn`
raises on a negative amount so a parser bug surfaces at the parser.

**`tag_source` is nullable, and `NULL` means "nothing has categorised this".**
There is deliberately no enum value for "the model was asked and failed" —
absence of a tag is how that is represented, and it is recoverable with
`wimmg tag`.

**Timestamps are naive UTC**, produced by `app.clock.utc_now()`. SQLite does not
preserve offsets, so supporting timezones halfway would mean comparing aware and
naive datetimes at runtime.

---

## Request paths

**`POST /ingest/file`** — sanitise the filename to a basename, write to a
temporary directory, sniff and parse, insert with dedupe, delete the temporary
copy, then run rule and model tagging on just the new rows.

**`GET /dashboard/summary`** — one query, aggregated in Python. At single-user
scale (thousands of rows) this is faster than several round trips and much easier
to read than the SQL equivalent.

**`GET /report/spend-analysis`** — gather aggregates, run the model passes
concurrently, build the PDF in a thread, stream it back.

**`POST /chat/stream`** — server-sent events. On model failure it emits an `error`
event rather than apologetic prose that reads like an answer.

---

## Testing

The suite runs without Ollama, using a fake model that can be put into a failure
state. That is what makes the interesting paths testable:

| File | Covers |
|---|---|
| `test_llm_contract.py` | Failure never returns plausible content; thinking is disabled; the schema is sent |
| `test_pipeline.py` | Offline model leaves rows untagged, not falsely tagged; recovery; idempotency; user tags survive |
| `test_ingest.py` | Identity hashing, dedupe, content-based parser detection, narration cleanup |
| `test_quality_gates.py` | Good prose passes the degeneracy gate — the regression that would have caught a real bug |
| `test_detectors.py` | Cadence detection; commitments excluded from subscriptions |
| `test_privacy.py` | No phone numbers, UPI handles or employer identifiers in source |
| `test_rules.py` | Rule matching, priority, taxonomy/database agreement |
| `test_demo_data.py` | Determinism, calendar correctness, balance coherence |
| `test_api.py` | Every endpoint, including path traversal and upload validation |

---

## What this architecture is not built for

Stated so the trade-offs are legible rather than looking like oversights.

- **One user, one machine.** No authentication, no tenancy, SQLite. Adding
  multi-user would mean row-level ownership on every query and a real database —
  a different project, and one that breaks the constraint this one exists for.
- **Not high throughput.** Categorising is one model call per transaction at about
  0.7s effective with concurrency 4. Fine for a year of personal statements; not
  fine for a million rows. Batching multiple transactions per call would trade
  accuracy for speed, and accuracy is the point.
- **Aggregation in Python, not SQL.** Deliberate at this scale, for readability.
  It would need moving into the database an order of magnitude further up.
- **`create_all`, not migrations.** The schema is created on startup. Alembic was
  a declared dependency that was never wired up, so it was removed rather than
  left as decoration; changing the schema today means `wimmg reset --all`. That is
  acceptable for a local single-user tool and would not be for anything shared.
