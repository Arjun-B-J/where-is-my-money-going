# Privacy

This app reads your bank statements. That makes privacy a design constraint
rather than a feature, so this document sets out the threat model, what the code
actually does, and how it is enforced.

---

## The threat model

The obvious risk is a statement file being uploaded somewhere. The less obvious
one, and the one that shapes most of the decisions here, is that **your statement
is not only about you**.

Indian UPI narrations embed the payee's name, and very often their mobile number:

```
UPI-SOME PERSON-9xxxxxxxxx@ybl-HDFC0001234-4837-PAYMENT
```

So a year of statements is also a contact list of everyone you have paid, with
amounts. Any decision about where this data goes has to account for the people who
never agreed to any of it.

That leads to two rules the codebase is built around:

1. **Nothing leaves the machine.** The only network call the backend makes is to a
   model you are running yourself.
2. **Nothing that identifies a real person belongs in this repository.** Not in
   source, not in tests, not in an image.

The second one needs enforcing rather than intending, because it is easy to
satisfy the letter of it and miss the point. Gitignoring `*.pdf` does nothing about
a screenshot: a PNG of your dashboard can carry names, phone numbers and your
income, and no file-extension filter will catch it. The checks below are written
against "any file that contains this information", not against a list of
extensions.

---

## What the code does

**Where your data goes.** Statements are parsed on your machine into a local
SQLite file. Categorising sends one transaction at a time to a model served by
Ollama on `localhost:11434`. That is the only outbound call, and it does not leave
the machine unless you point `LLM_HOST` somewhere else yourself.

**What the model sees.** One transaction's description, amount, direction and
account, per call. The report's prose and the chat feature get aggregates only:
totals, top categories, top payees. Chat is deliberately built on the summary
rather than the raw rows, which means the feature never hands a model a list of
everyone you have paid.

**Uploads are not retained.** `POST /ingest/file` parses from a temporary
directory that is deleted as soon as the rows are extracted. The transactions are
kept; the statement is not. Filenames are reduced to a sanitised basename first,
so a crafted name cannot decide where anything is written.

**Nothing personal in shipped code.** Anything specific to one person's life lives
in your database, not in source:

- The `rules` table holds your tagging rules, including any keyed on a name.
- The `merchant_notes` table holds the annotations that appear in the report's
  "why notable" column. `PUT /merchant-notes` is the supported way to add one.
- Salary is identified by the `salary` category, not by matching a payroll
  descriptor.
- `TxnSource` is generic: `bank`, `card`, `bank_2`. Never a bank's name, never an
  account number. Display labels are one editable dictionary in
  `backend/app/reports/labels.py`.
- The seed data creates no people at all. People are discovered from your
  transactions by `app.services.friend_detector`.

**No telemetry.** No analytics SDK, no error reporting, no external fonts, no CDN.
The frontend sets `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.

**Redaction where output gets shared.** `redact_payee()` reduces person-shaped
names to initials in CLI output, so `wimmg patterns` is safe to paste or
screenshot. It is deliberately biased towards over-redacting: abbreviating a shop
name costs nothing, and the reverse mistake is the one that matters.

**The demo dataset is fictional, and obviously so.** The people in it are famous
footballers, the phone numbers use an unassignable `90000 000xx` pattern, the UPI
handles are `@okbank`, and every row is tagged `synthetic: true` in the database.
Recognisable names are the point: nobody can mistake a demo screenshot for a real
ledger.

---

## How it is enforced

`backend/tests/test_privacy.py` runs as its own CI job, and fails the build on:

| Check | What it catches |
|---|---|
| No Indian mobile numbers in source | `9xxxxxxxxx` patterns. The demo dataset's unassignable `90000 000xx` range is allowlisted |
| No real UPI handles in source | `@oksbi`, `@okicici`, `@ybl`, `@paytm` and friends. Demo data uses `@okbank`, which is not a real handle |
| No employer identifiers | Payroll descriptors and employer names |
| Seeded rules name no individuals | A default rule may not carry a `person_name` |
| Seeded payee notes are structural | Allowlisted to transaction *types* (`SALARY`, `ATM`, `RENT`), never a specific payee |
| The committed sample report is synthetic | Its text is checked for demo payees, and for absence of phone numbers and real handles |

Plus a repository-level check in the same job:

```bash
git ls-files | grep -iE '\.(pdf|csv|xlsx?|db|sqlite3?)$'
```

Exactly one path is allowed through that filter, `docs/sample-report.pdf`, and the
test above asserts it came from the demo dataset.

**Screenshots** in `docs/screenshots/` are generated by `wimmg demo` followed by
the Playwright capture script in `frontend/tests/e2e/screenshots.spec.ts`, against
the synthetic dataset only. Regenerating them is a scripted step rather than a
manual redaction pass, because manual redaction is the part a human gets wrong.

**What is gitignored.** `*.pdf`, `*.csv`, `*.xlsx`, `*.db`, `*.sqlite*`, the
`statements/`, `documents/` and `data/` directories, `storage/`, `.env`, and
generated report output. Keeping statements in a folder outside the repository
entirely is safer still, and every CLI command accepts an arbitrary path.

---

## Limits you should know about

- **No authentication and no encryption at rest.** The SQLite file is plaintext on
  your disk, protected by your operating system's file permissions and nothing
  else. Full-disk encryption is the right control, and it is your machine's job.
- **It binds to localhost and assumes one user.** Do not expose it to a network
  as-is. There is no login, and every endpoint reads and writes the same dataset.
- **Backups are your problem.** Nothing is synced anywhere, by design. That is also
  true of the copy you would want if the disk died.
- **`LLM_HOST` is a loaded gun.** Point it at a remote endpoint and your
  transaction descriptions go there. Nothing stops you. The health endpoint always
  shows which host is configured.

---

## Verifying it yourself

```bash
# What the backend talks to
grep -rn "http" backend/app --include=*.py | grep -v localhost

# No third-party telemetry anywhere
grep -rEi "posthog|sentry|gtag|analytics|googleapis|mixpanel" \
  backend/app frontend/app frontend/components frontend/lib

# The privacy checks
cd backend && python -m pytest tests/test_privacy.py -v

# Nothing data-shaped is tracked, other than the sample report
git ls-files | grep -iE '\.(pdf|csv|xlsx?|db|sqlite3?)$'
```

## Reporting a problem

If you find personal data anywhere in this repository, or a way for data to leave
the machine that is not documented above, please email the address in
[SECURITY.md](../SECURITY.md) rather than opening a public issue. I would much
rather hear it from you.
