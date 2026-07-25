# Security

## Scope

This is a local-first, single-user application. It binds to `localhost`, has no
authentication, and stores data in a plaintext SQLite file on your own disk. That
is the intended design, not an oversight — see
[docs/PRIVACY.md](docs/PRIVACY.md#limits-you-should-know-about) for the full list
of limits.

**Do not deploy it on a network as-is.** Every endpoint reads and writes the same
single-user dataset with no access control.

## What I am most interested in hearing about

In rough order of severity:

1. **A way for data to leave the machine** that is not documented — any network
   call other than to the configured `LLM_HOST`.
2. **Personal data in this repository.** Anything in source, tests, docs or an
   image that identifies a real person. This has happened before
   ([docs/PRIVACY.md](docs/PRIVACY.md)) and I would much rather hear it from you.
3. **Path traversal or arbitrary write** through the upload endpoint. Filenames
   are reduced to a sanitised basename and parsed from a temporary directory, but
   that is one function worth a second pair of eyes:
   `backend/app/routes/ingest.py`.
4. **Anything that lets a crafted statement execute code.** Parsing runs regexes
   over `pdfplumber` text output; a malicious PDF is a real input to consider.
5. **Prompt injection through a transaction description.** A description is
   attacker-controlled if someone can choose what appears on your statement.
   Categorising is schema-constrained, so the blast radius should be limited to a
   wrong category — a way to get further would be worth knowing.

## Reporting

Email **reachtoarjunbj@gmail.com** rather than opening a public issue, and give me
a reasonable window to fix it before disclosure.

For anything not sensitive — a crash, a bad parse, a wrong number — a public
[issue](https://github.com/Arjun-B-J/where-is-my-money-going/issues) is better,
because someone else has probably hit it too.

## Supported versions

The `master` branch. This is a personal project, not a product with releases.
