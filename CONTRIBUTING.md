# Contributing

The most useful contribution is **a parser for a bank this does not support yet**.
Three parsers covering one bank is the honest limit of the project today, and
[docs/EXTENDING.md](docs/EXTENDING.md#4-adding-a-parser-for-a-new-bank) walks
through adding one step by step — it is about 80 lines.

## Setting up

```bash
make install     # backend venv + npm install
make model       # ollama pull gemma4:26b, if you want the model locally
make check       # lint, types and tests for both sides
```

`make check` runs exactly what CI runs, so a green local run means a green
pipeline. The test suite does **not** need Ollama: it uses a fake model that can
be put into a failure state, which is how the "model unavailable" paths get
covered.

## Before you open a pull request

- `make check` passes.
- New behaviour has a test. New *fixed* behaviour has a test that would have
  failed before the fix.
- **No real data.** No statements, no databases, no screenshots of your own
  spending. Remember that a screenshot is an image, so no extension filter catches
  it. CI fails if any `.pdf`/`.csv`/`.db` is tracked, and
  `backend/tests/test_privacy.py` fails if a phone number, real UPI handle or
  employer identifier appears in source. Build fixtures inside the test instead;
  see `test_parses_card_csv` for the pattern.

## Things this project will not accept

The reasoning for each is in [docs/DECISIONS.md](docs/DECISIONS.md).

**A model producing a number.** Amounts, dates and balances come from
deterministic code. The model classifies rows that already exist.

**A fallback that looks like a real answer.** If a model call fails, return
`ok=False` and write nothing. An earlier version returned
`{"category": "uncategorized", "confidence": 0.0}` instead, which put 285
unclassified rows into the database labelled as model output.

**A personal detail in source code.** Not a payee's name, not an employer's
payroll descriptor, not an account number. The `rules` and `merchant_notes` tables
exist for exactly that.

**A quality gate with no test proving good input passes it.** The prose gate here
rejected every valid paragraph and nothing looked broken, because the fallback read
well enough.

## Style

Backend is ruff-linted and mypy-checked; frontend is ESLint and `tsc --noEmit`.
Beyond that: comments explain *why*, not what. If a constant has a specific value
for a reason, write the reason next to it.

## Reporting problems

Bugs and parser requests: open an issue. Anything involving data exposure:
[SECURITY.md](SECURITY.md).
