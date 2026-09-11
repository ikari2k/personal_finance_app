# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 0–1 are complete: the `uv` project/directory tree, a Jinja2-rendered `/health` endpoint,
PID-based file locking, ledger/account/category read-write storage, and the ledger consistency
check (wired into a non-blocking startup hook) all exist and are covered by passing tests. Phase 2
(accounts, manual transaction entry, transfers, balances) has not started. See
`docs/implementation-plan.md` for the full phased plan, finalized schemas, and per-phase status
checkboxes/implementation notes.

**Work proceeds one phase at a time.** Each phase in `docs/implementation-plan.md` is a discrete,
separately-reviewable unit — implement it, verify it, stop, and update docs (this file plus the
plan's checkbox/status) before starting the next phase. Don't jump ahead to a later phase's work
while an earlier one is in progress.

**Environment note**: `uv` is only on PATH inside this repo, via the `.python-version` file
pinning it to pyenv's Python 3.13.7 (the pyenv-global Python doesn't have `uv` installed). Run
`uv`/`uv run` commands from the repo root.

## What this app is

A local, single-user personal finance tracker. No login, no multi-user support, no remote
database, no bank API integrations (Plaid, etc.), no network access beyond `localhost`. All data
lives in plain text files on disk — CSV for the transaction ledger, TOML for configuration. The
app itself is the sole writer of these files; manual editing while the app is running is
unsupported.

Stack: Python 3.11+ / FastAPI backend, Jinja2 + HTMX for server-rendered UI (minimal JS), CSV/TOML
for storage (no database), `uv` for dependency management, a launcher script that starts Uvicorn
and opens the browser.

## Commands

```
uv sync                                    # install/update dependencies
uv run uvicorn app.main:app --reload       # run dev server
uv run pytest                              # run full test suite
uv run pytest tests/unit -k ledger         # run a filtered subset
uv run pytest tests/unit/test_x.py::test_y # run a single test
uv run ruff check .                        # lint
uv run ruff format .                       # format
uv run python scripts/seed_sample_data.py  # reset data/config to fake sample data (Phase 2+)
uv run python scripts/launch.py            # one-click launcher (starts server + opens browser)
```

## Project layout

```
app/
  main.py         # FastAPI app factory, startup consistency check
  config.py        # resolves data/config paths, binds 127.0.0.1 only
  models/           # pydantic domain models (Account, Category, Transaction, Rule, ImportMapping)
  storage/           # file I/O + locking — the ONLY layer allowed to touch data/ or config/ files
  services/            # business logic (transfer pairing, consistency checks, aggregation) — no direct file I/O
  routers/              # FastAPI routers, one per feature area — thin HTTP/HTMX glue only
  templates/             # Jinja2 pages + HTMX partials
  static/                # css, vendored chart JS (no CDN — app must run offline)
data/ledger.csv           # created on first run if absent
config/                     # accounts.toml, categories.toml, rules.toml, import_mappings/<bank>.toml
tests/unit/                  # storage/ + services/, tmp_path-isolated, never touches real data/config
tests/integration/            # router-level, FastAPI TestClient
scripts/seed_sample_data.py    # dev-only: resets data/config to fake sample data (Phase 2+)
scripts/launch.py              # starts uvicorn, waits for readiness, opens browser
```

`storage/` owns all disk access and file locking. `services/` holds business rules and must stay
unit-testable without touching disk (inject paths/tmp_path in tests). `routers/` stays thin.

### Storage module pattern (established in `app/storage/{lock,ledger,accounts,categories}.py`)

Every `storage/*.py` read/write module follows the same shape — reuse it rather than inventing a
new one for `rules.py` / `import_mappings.py` in later phases:

- `read_x(path: Path = DEFAULT_PATH) -> ...`: returns an empty list/dict if `path` doesn't exist
  yet (files are created lazily on first write, never at startup).
- `write_x(items, path: Path = DEFAULT_PATH) -> None`: wraps the write in
  `app.storage.lock.file_lock(path)`, writes to a `<path>.tmp` sibling, then `Path.replace()`s it
  into place — atomic, so a crash mid-write can never leave a truncated/partial file.
- Default `path` args come from `app/config.py`, so callers rarely need to pass one explicitly;
  tests always pass an explicit `tmp_path`-based path instead.
- Money fields (e.g. `Account.starting_balance`) are pydantic `Decimal`, but serialize to TOML as
  a **quoted string**, not a bare float — `tomli_w` writes `Decimal` as a bare TOML float literal
  and `tomllib` reads it back as Python `float`, risking silent precision loss. The same care is
  needed for `Transaction.amount` in the ledger CSV, which is already string-typed there.

## Core architecture

### Data files

- `config/accounts.toml` — `[[accounts]]` tables: `id, name, number, description,
  starting_balance`. `id` is a stable short code that **never changes** even if name/description
  does — transactions reference `id`, never the account name.
- `config/categories.toml` — mapping of category name → list of subcategory names. Editable by
  hand, via settings UI, or on the fly during transaction entry/import. No automatic dedup — the
  app does not tidy this up.
- `data/ledger.csv` — single flat file, one row per transaction, columns (finalized order):
  `id, date, account_id, category, subcategory, description, amount, type, transfer_id, notes`.
  `amount` is a signed decimal string; `type` ∈ `income|expense|transfer`. This is the one file
  the whole app revolves around.
- `config/import_mappings/<bank_name>.toml` — `delimiter, date_format, columns` (bank column →
  ledger field map) plus an optional bank-scoped `[[rules]]` list. Captured on first import,
  reused automatically on subsequent imports from that bank.
- `config/rules.toml` — `[[rules]]` tables: `pattern, field, category, subcategory, priority`.
  Shared between import-time auto-categorization and bulk reclassification of existing rows.

### Key invariants to preserve when touching ledger/account code

- **Transfers are two linked rows**, not one: an outflow row in the source account and an inflow
  row in the destination account, joined by a shared `transfer_id`, with opposite-sign amounts.
  Never model a transfer as a single row, and never let one side of a transfer pair be
  edited/deleted without the other.
- **File locking on every write** to `ledger.csv` and config files being edited, via
  `app.storage.lock.file_lock` (a `<path>.lock` sibling file storing the writer's PID). Acquiring
  the lock checks whether that PID is still alive (`os.kill(pid, 0)`) and auto-clears the lock
  file if not — no manual lock-file cleanup should ever be required. A lock still held by a live
  process raises `LockError` after a timeout rather than blocking forever.
- **Referential integrity is enforced in application code**, not a database (there is no DB).
  `services/consistency.py::check_consistency(transactions, accounts)` checks for orphaned
  transfers and invalid `account_id` references; `app/main.py`'s `lifespan` hook runs it
  automatically (non-blocking, warnings-only) on every startup. The on-demand "Check consistency"
  action still needs a router + UI — add it once a settings/reports router exists. Any change to
  transfer or account-reference logic should keep this check in mind.
- **Regex rules must fail loudly** on invalid patterns — compile-check at save time, never
  silently match zero rows at apply time.
- **Bulk reclassification always previews first**: a rule run against the ledger must show a full
  before/after diff per affected row (old category/subcategory → new, plus any other changed
  fields), never just a count. Preview output must match apply output exactly.
- Reports pull from **one shared aggregation layer** (`services/aggregation.py`, group-by
  year/month/category/subcategory), not per-view one-off aggregation logic — wire new report
  views through it rather than duplicating grouping logic.
- Charting is done with a **vendored JS lib in `app/static/`**, not a CDN script tag — the app
  must run with no network access.

### Scale assumptions

~7,000–9,000 ledger rows at steady state (5 years × ~100–150 tx/month). Loading the full CSV into
memory per request is an accepted, deliberate simplification — don't add indexing, pagination, or
a caching layer to "fix" this.

## Code style

All Python follows PEP 8, enforced via `uv run ruff check .` / `uv run ruff format .`. Public
modules, classes, and functions get PEP 257-style docstrings (purpose, params, returns) — skip
them only for trivial one-liners where the signature is self-explanatory. Type hints on all
function signatures.

## Build phases

See `docs/implementation-plan.md` for the full phase-by-phase plan and status:

0. Scaffolding (`uv` project, directory tree, health-check app)
1. Core ledger read/write + file locking + consistency check
2. Accounts, manual transaction entry, transfers, balance calculation
3. Bank CSV import (mapping setup + reuse, import-time auto-categorization)
4. Rule engine (bulk reclassification with preview/apply)
5. Reporting & visualization (shared aggregation layer, drill-downs, MoM/YoY, net worth charts)
6. Launcher script + error-handling pass
