# Decisions

The non-obvious calls, why they were made, and what would make me change my mind.

Several of these entries are corrections. Where a decision replaced an earlier one
that turned out to be wrong, the wrong version is described too — the reasoning
that produced a bug is more useful than a clean statement of the fix.

---

## 1. Deterministic extraction, then the model

**Decision.** Amounts, dates and balances are parsed by Python and regexes. The
model only ever sees rows that have already been extracted, and only to assign a
category.

**Why.** There is no prompt good enough to make a hallucinated rupee figure
acceptable in a tool about money. The failure mode is silent and the output looks
plausible, which is the worst combination. Extraction is also genuinely easy —
bank statements are machine-generated and regular — so handing it to a model
trades a solved problem for an unsolvable one.

The side benefit is that the slow, non-deterministic part is isolated. Parsing a
year of statements takes under a second and is fully testable; categorising takes
minutes and needs a fake in tests.

**Would revisit if.** Never for numbers. Possibly for *layout* detection on a
statement format too irregular to regex, where a vision model identifies column
boundaries and deterministic code still reads the values.

---

## 2. Direction comes from the running balance

**Decision.** For account statements, whether a row is a debit or a credit is
inferred from the change in closing balance, not from which column the number was
in and not from keywords.

**Why.** `pdfplumber` extracts text, not a grid. The withdrawal and deposit
columns collapse into one whitespace-separated run, so column position is gone by
the time you see the data. Keyword matching on the narration fails in both
directions — "PAYMENT" appears in refunds, "CR" appears inside reference numbers.

The closing balance is the one number the bank guarantees is arithmetically
correct. Diffing consecutive rows gives direction for free and eliminates a whole
class of mis-tagging.

**Cost.** It needs rows in statement order, and it cannot help with the very first
row, which has no predecessor — that one falls back to keywords. Card statements
have no balance column at all, so they use the `+` marker the statement prints
instead.

**Consequence for the demo data.** The synthetic generator applies its running
balance *after* sorting, in real chronological order. An earlier version computed
it during generation, so the balance column contradicted the timeline and would not
have exercised this inference at all.

---

## 3. `think=false`, and a JSON Schema rather than `format="json"`

**Decision.** Classification calls pass a full JSON Schema, with `enum` on the
category field, and disable the model's thinking channel.

**One caveat if you copy this.** These two settings used to fight each other.
Ollama deferred the schema's probability masking until it saw an end-of-thinking
token, so `think=false` closed the thinking tags early and the constraint was
silently dropped ([ollama#15260](https://github.com/ollama/ollama/issues/15260)).
That is fixed, and the combination is verified working here on 0.23.1, but on an
affected build you get plain text back and no error to tell you why.

**Why — and this is the big one.** The default model, `gemma4:26b`, is a reasoning
model. The first version of this app used Ollama's `format="json"`, which asks for
syntactic JSON and nothing more. With thinking enabled, its content channel
degenerated:

```
{"category":"Transaction_Type_Classification_Classification_of______________
```

Every call failed to parse. Because of the fallback described in §4, each failure
was recorded as a real low-confidence answer, and the dashboard reported that 80%
of spending was uncategorized. I spent a while assuming the model was simply bad at
Indian merchant names.

Probing the four combinations directly settled it:

| thinking | format | result |
|---|---|---|
| on | `"json"` | invalid — degenerate loop mid-string |
| on | schema | invalid — truncated mid-string |
| off | `"json"` | valid, but off-taxonomy: returned `"Food & Dining"` |
| **off** | **schema** | **valid, on-taxonomy, ~1.7s** |

Both halves matter. `think=false` fixes validity; the schema's `enum` is what
holds the model to the sixteen categories the database actually knows about.
Without it the model invents its own names and the UI cannot render them.

The measured result on 1,004 synthetic transactions: every row categorised, high
confidence on recognisable merchants, and 0.3–0.4 on deliberately opaque payees.
Confidence became meaningful rather than decorative.

**Also.** `maxLength` on free-text fields. Constrained decoding bounds structure
but not string length, and an unbounded `reason` field is the one place output
still runs away.

**Would revisit if.** A future model needs thinking to classify well. `LLM_THINK`
is a setting for exactly that, and [EXTENDING.md](EXTENDING.md#8-swapping-the-model)
documents what to re-verify.

---

## 4. A failed call returns nothing, and nothing is written

**Decision.** `LLMClient` returns `LLMResult(ok=False, text="")` on any failure.
When classification fails, no tag is written: `category` and `tag_source` stay
`NULL`, `needs_review` is set, and the run records `llm_available: false` with a
count.

**Why.** The version this replaced returned a hand-written stub whenever the model
was unreachable or its reply failed to parse:

```python
return json.dumps({"category": "uncategorized", "confidence": 0.0,
                   "reason": "Ollama unavailable — falling back."})
```

The caller then stamped `tag_source = TagSource.LLM` on it. 285 transactions ended
up in the database indistinguishable from genuine low-confidence answers, and the
UI displayed them as model output at 0% confidence.

That fallback was written to be helpful — to keep the pipeline running rather than
crash on a first run while the model downloads. It achieved that, and in exchange
it destroyed the ability to tell "the model is unsure" from "the model never ran",
which is the single most important distinction this app makes. **A fallback that
imitates success hides the bug that caused it.**

The `TagSource` enum deliberately has no value meaning "attempted and failed".
Absence of a tag is how that state is represented, and it is recoverable —
`wimmg tag` picks up exactly those rows once the model is back.

**Cost.** A first run with no model produces a database of uncategorised
transactions. That is the honest outcome, it is stated on the run record and in the
CLI output, and the recovery is one command.

---

## 5. A quality gate must be tested against known-good input

**Decision.** `looks_degenerate()` checks for a word repeated three times
consecutively, or a 3-word phrase repeated four or more times. The first test in
its suite asserts that a real paragraph passes.

**Why.** The version this replaced counted repeated 4-character windows and
rejected any text where one appeared 8+ times:

```python
for window in (4, 6, 8):
    ...
    if any(count >= 8 for count in seen.values()):
        return False
```

Every English paragraph of a few hundred characters trips that on `" the"` and
`"tion"`. The gate rejected **all** valid prose, so the report's model-written
narrative was discarded 100% of the time and a deterministic fallback was used on
every run. Nothing looked broken, because the fallback read reasonably well.

Two lessons, both encoded in the code now. A gate that can reject good output is
worse than no gate, because it silently disables the path it was protecting. And
prose belongs in plain text, not inside a JSON string field — a paragraph that runs
past its closing quote destroys the whole object, which is what the character
windows were really reacting to.

---

## 6. Generated text says whether a model wrote it

**Decision.** Narrative blocks and insight cards carry
`generated_by: "model" | "computed"`. The report prints a line saying so when a
section was assembled from aggregates, and the API exposes the field.

**Why.** Deterministic fallback text is genuinely useful — it is factual and
specific. Presenting it as analysis the model performed is a small lie that
compounds: it hides model outages, and it makes the feature impossible to evaluate.
Labelling it costs one field and makes both problems visible.

---

## 7. One identity hash, and duplicate coffees collapse

**Decision.** `external_id = sha1(source | date | amount | lowercased description)`,
with the date truncated to the day.

**Why.** Bank statements rarely carry a usable unique reference, and the same
transaction gets a different timestamp between exports. These four fields are the
ones that do not change, which makes re-ingestion a no-op — the property that lets
you re-run the same command every month without thinking about it.

**Known cost, accepted deliberately.** Two genuinely distinct transactions sharing
all four fields — the same coffee twice in one day at the same price — collapse into
one. That is the right trade: silently doubling someone's spending total is a worse
failure than dropping one duplicate coffee, and the alternative (including a
sequence number) breaks idempotency, which is the whole point.

Dedupe also runs *within* a batch, not just against the database, because
overlapping statement date ranges are the normal case rather than an edge case.

---

## 8. Detectors are deterministic; only classification uses the model

**Decision.** Instalment plans, subscriptions, duplicate charges, unmatched
refunds, friend detection and cross-statement reconciliation are all plain Python.
The model classifies categories and writes prose.

**Why.** Each finding is shown to a user as "worth checking", which is only
defensible if it can be traced to specific rows. "Two charges of ₹1,299 forty
seconds apart at the same merchant" is a fact. A model asserting the same thing is
a claim.

They are also the parts most worth testing, and deterministic code can be tested
without a model.

**Where the model does help.** Direction of a *relationship* — lending versus
borrowing versus splitting bills — reads better from a time series than from a rule,
and being wrong there is low-stakes. That one is `pipeline/validator.py`, and its
output is sanity-checked before it is stored.

---

## 9. Friend detection is structural, then confirmed

**Decision.** A counterparty is a candidate person if money flowed **both**
directions, the name looks like a person, and the handle looks like a personal UPI
address. The model then classifies the relationship.

**Why.** Bidirectionality is the strong signal, and it is cheap. Merchants
overwhelmingly take money in one direction; refunds are rare and rarely balanced.
Combining it with a person-shaped name and a personal handle gets the false
positive rate low enough to auto-create records, with a confidence score gating
whether that happens.

**Cost.** Someone you have paid but never received money from will not be detected.
That is the correct default — the alternative is treating every payee as a person.

---

## 10. Real files go through the same pipeline as demo data

**Decision.** The LangGraph graph branches after seeding: `mode="demo"` generates,
`mode="files"` parses a directory. Both converge on the same categorising path.

**Why.** They used not to. The graph could only generate synthetic data, and real
statements went through a standalone script that reimplemented the tagging loop.
Two code paths meant the documented pipeline and the one that actually ran on real
data could drift — and they had. The README described a flow that real ingestion did
not follow.

One conditional edge is much cheaper than two implementations of the same thing.

---

## 11. One CLI, no scripts directory

**Decision.** `wimmg <command>`. The seven scripts in `scripts/` are gone.

**Why.** Each had accumulated its own copy of the tagging loop, its own session
handling and its own argument parsing. Fixing a bug in the pipeline did not fix it
for whoever ran the script. They also could not be tested, because they were
`__main__` blocks with side effects.

One of them, `finalize_demo_state.py`, deserves specific mention. It marked
untagged rows as model-tagged with zero confidence and wrote invented per-node
timings (`"llm_tag": 482_193`) onto a run record so the pipeline page would look
complete in screenshots. It has no replacement. Fabricating an audit trail to make
a demo look finished is not a feature, and a project about being honest with
numbers cannot ship it.

---

## 12. Naive UTC everywhere

**Decision.** Every timestamp is a naive `datetime` in UTC, produced by
`app.clock.utc_now()`.

**Why.** SQLite does not preserve timezone offsets. Storing aware datetimes means
reading back naive ones, and mixing the two raises `TypeError` the moment you
compare them — which, in a codebase full of date arithmetic across fifteen service
modules, would surface at runtime in a chart rather than at the boundary.

`datetime.utcnow()` is deprecated in 3.12+, and calling it here emitted roughly
64,000 warnings per test run, which is functionally the same as emitting none. The
helper fixes both. Warnings are now errors in the pytest config so the next
deprecation is a failure rather than noise.

---

## 13. One report, not two

**Decision.** A single Spend Analysis PDF.

**Why.** There were two generators, 1,548 lines between them, duplicating palette,
chart helpers and table styling. One of them — a "dashboard digest" — restated what
the web dashboard already showed, in a worse medium. Deleting it was better than
merging it. What remains is split into `theme`, `charts`, `labels`, `narrative` and
document assembly, so the layout code reads as layout.

---

## 14. Canvas and CSS transforms, not a 3D library

**Decision.** The landing page's hero animation is a hand-written 2D canvas with
perspective projection; the cards tilt using CSS `rotateX/rotateY`.

**Why.** three.js is roughly 600 KB gzipped — more than the rest of this app — for
an effect that is decorative. A personal-finance tool that takes three seconds to
paint on a mid-range phone reads as unserious no matter how good the shader is. The
depth here comes from Bézier projection, size falloff and layered alpha, which cost
a transform per frame and composite on the GPU.

The animation also earns its place by being about the product: money entering at
one point and sorting itself into named streams is the thing the app does. Both
respect `prefers-reduced-motion`, and the tilt is disabled where there is no cursor
to track.

---

## 15. No vector search, and where it would actually earn its place

**Decision.** There is no embedding index and no retrieval step. Every model call
gets exactly the context it needs, assembled by SQL.

**Why not.** Retrieval solves a specific problem: the relevant context does not
fit, and you do not know in advance which part you need. Neither is true here.

* **Categorising** sees one transaction of about fifty characters. There is
  nothing to retrieve — the entire input is already in the prompt. Adding a
  retrieval hop would add latency and a failure mode for no gain.
* **Detectors** are arithmetic over the full table. Approximate nearest-neighbour
  search is a strictly worse way to compute a sum than computing the sum.
* **Report prose and insight cards** are given aggregates that fit comfortably. The
  hard part there was making the model's output *valid*, not finding the input.

A finance tool has a specific reason to be careful about this: retrieval returns
*most similar*, not *complete*. "How much did I spend on food" answered from the
top-k similar rows is a number that is confidently wrong, and there is no way for
the user to tell. `SELECT SUM(...) GROUP BY category` is exact. Swapping an exact
aggregate for a similarity search would be a downgrade dressed as sophistication.

**Where it genuinely fits, in order of how much I want it.**

**1. Retrieving the user's own past decisions as few-shot examples.** This is the
real one, and it closes an actual gap: today, correcting a category in the review
queue teaches the system nothing. Tag `UPI-S KUMAR` as `rent` fifty times and the
fifty-first is still `uncategorized` at 0.35 confidence.

The fix is retrieval, over the user's own confirmed tags rather than over
documents: embed transaction descriptions, and before classifying a new row,
retrieve the nearest rows where `tag_source = 'user'` and put them in the prompt as
examples. The review queue becomes training signal instead of a chore, and it stays
local — a small embedding model in Ollama, vectors in the same SQLite file.

It also composes with what is already here rather than replacing it: exact
aggregates stay exact, and retrieval only ever influences a *label*, never a
number. That is the boundary that makes it safe.

**2. Merchant canonicalisation.** `SWIGGY`, `SWIGGY LIMITED` and `BUNDL
TECHNOLOGIES` are one merchant, and no regex will ever know that. Embedding payee
strings and clustering them would merge variants, which improves top-payee
aggregation, subscription detection and category consistency at once. Narrow,
measurable, and no model output reaches a number.

**Where it looks like a fit but is not: chat.** Chat is the obvious candidate —
today it answers from a fixed summary, so "what did I buy in March" is out of
reach. But the right tool there is **tool-calling over the database**, not vector
search: let the model emit a filter and run a real query. The questions people ask
about money are filters and aggregates, and those have exact answers. Semantic
retrieval would give a plausible-looking approximation of a question SQL answers
precisely — which is the same mistake as §4, in a different costume.

**Revisit if.** The review queue accumulates a few hundred user corrections. That
is the point at which few-shot retrieval has enough signal to beat the current
zero-shot prompt, and enough data to measure whether it actually does.

---

## 16. Uploaded statements are parsed and thrown away

**Decision.** `POST /ingest/file` writes to a temporary directory that is deleted
when parsing finishes. Filenames are reduced to a sanitised basename first.

**Why.** Two things. A client-supplied filename joined onto a path lets `../../`
escape the upload directory — the previous version did exactly that. And once the
rows are extracted, a second copy of the statement on disk is pure liability: it is
the most sensitive artefact the app touches and nothing needs it again.
