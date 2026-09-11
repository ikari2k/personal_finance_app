# Implementation Plan — Personal Finance Tracker

This elaborates `finance-app-prd.md` into concrete technical decisions and a phased build order.
Phases are implemented one at a time, each its own reviewable unit of work with its own commit(s).
`CLAUDE.md` is updated after each phase lands to reflect what became concrete during that phase.

**Status**: Phase 0 complete. Phase 1 not yet started.

**Environment note**: `uv` is installed under pyenv's Python 3.13.7, not globally on PATH — the
`uv` shim only resolves once a directory is pinned to that pyenv version. This repo has a
`.python-version` file (`3.13.7`) at its root for exactly this reason; from inside the repo,
`uv`/`uv run ...` work as documented. If commands report `uv: command not found`, confirm you're
in the repo root and that `.python-version` is present.

## Locked-in technical decisions

- **Package/env manager**: `uv`. `pyproject.toml` + `uv.lock`. Python 3.11+ (stdlib `tomllib`
  for TOML reads; `tomli_w` for writes, since `tomllib` is read-only).
- **Web**: FastAPI + Jinja2 templates + HTMX. `python-multipart` for form/file uploads.
- **Testing**: `pytest`, plus FastAPI `TestClient` (httpx) for router-level tests. Storage/service
  unit tests use `tmp_path` fixtures — never touch the real `data/`/`config/` dirs.
- **Lint/format**: `ruff check` + `ruff format`.
- **Charting**: vendor a single-file JS charting lib into `app/static/` rather than loading from
  a CDN — the app must run fully offline. Resolves the PRD's open charting question.
- **Consistency check timing**: runs automatically (non-blocking) on startup *and* on demand via
  a "Check consistency" action — resolves the PRD's open question as both, not one or the other.
- **Ledger CSV columns** (finalized): `id, date, account_id, category, subcategory, description,
  amount, type, transfer_id, notes`. `amount` is a signed decimal string; `type` ∈
  `income|expense|transfer`.
- **TOML schemas** (finalized):
  - `config/accounts.toml`: `[[accounts]]` tables — `id, name, number, description,
    starting_balance`.
  - `config/categories.toml`: mapping of category name → list of subcategory names.
  - `config/rules.toml`: `[[rules]]` tables — `pattern, field, category, subcategory, priority`.
  - `config/import_mappings/<bank>.toml`: `delimiter, date_format, columns` (bank column →
    ledger field map) plus an optional bank-scoped `[[rules]]` list.

## Project layout

```
finance_app/
  pyproject.toml
  uv.lock
  app/
    main.py            # FastAPI app factory, startup consistency check
    config.py           # resolves data/config paths, binds 127.0.0.1 only
    models/              # pydantic domain models (Account, Category, Transaction, Rule, ImportMapping)
    storage/              # file I/O + locking — the only layer that touches disk
      lock.py              # PID-based lock context manager
      ledger.py
      accounts.py
      categories.py
      rules.py
      import_mappings.py
    services/               # business logic, no direct file I/O
      transactions.py        # create/edit txn, transfer pairing
      balances.py             # running balance + net worth
      consistency.py           # orphaned-transfer / invalid-account_id checks
      importer.py               # CSV parsing via saved/new mapping
      categorizer.py             # regex rule matching, preview/apply diff
      aggregation.py              # shared group-by year/month/category/subcategory
    routers/                      # FastAPI routers, one per feature area, HTMX fragment responses
    templates/                    # Jinja2 pages + HTMX partials
    static/                       # css, vendored chart JS
  data/
    ledger.csv                    # created on first run if absent
  config/
    accounts.toml
    categories.toml
    rules.toml
    import_mappings/
  tests/
    unit/                         # storage/ + services/, tmp_path-isolated
    integration/                  # router-level, FastAPI TestClient
  scripts/
    launch.py                     # starts uvicorn, waits for readiness, opens browser
```

`storage/` is the only code allowed to open `data/` or `config/` files, and owns all locking.
`services/` holds business rules (transfer pairing, consistency checks, aggregation) and stays
unit-testable without touching disk. `routers/` stays thin — HTTP/HTMX glue only.

## Phases

- [x] **Phase 0 — Scaffolding**: `uv init`; `pyproject.toml` deps (fastapi, uvicorn, jinja2,
  python-multipart, tomli-w, pydantic) and dev deps (pytest, httpx, ruff); directory tree above;
  FastAPI app serving a health-check page. *Verify*: `uv run uvicorn app.main:app --reload`
  serves a page.
  - `app/main.py` wires a Jinja2-rendered `/health` page and mounts `app/static/` — proves out
    the templating/static pipeline later phases build on.
  - `app/config.py` centralizes all `data/`/`config/` paths and the loopback-only host/port.
  - Ruff configured with the `D` (pydocstyle) rule set enabled, so missing/malformed docstrings
    fail lint rather than relying on manual review.
  - Added `.gitignore`: `data/*` and `config/*` (real financial data and generated per-bank/rule
    config) are excluded from version control, with `.gitkeep` files preserving the directory
    structure.
  - `pyproject.toml` sets `pythonpath = ["."]` under `[tool.pytest.ini_options]` so `tests/` can
    `from app... import ...` without installing the project as a package.
  - `tests/integration/test_health.py` exercises the endpoint via FastAPI `TestClient`.

- [ ] **Phase 1 — Core ledger read/write + locking**: `storage/lock.py` (PID-based stale-lock
  recovery), `storage/ledger.py` + `storage/accounts.py` + `storage/categories.py`,
  `services/consistency.py` (orphaned transfers, invalid `account_id`). *Verify*: `uv run pytest
  tests/unit -k ledger`; stale-PID lock test (spawn+kill a process, confirm lock clears).

- [ ] **Phase 2 — Accounts, transactions, transfers (manual entry)**: account CRUD
  router/templates, manual transaction entry with on-the-fly category/subcategory creation,
  transfer entry (`services/transactions.py` builds the linked two-row pair),
  `services/balances.py`. *Verify*: integration test confirms both transfer rows share
  `transfer_id` with opposite-sign amounts; balance reflects `starting_balance` + rows.

- [ ] **Phase 3 — Bank CSV import**: upload + parse endpoint, mapping-setup UI persisted to
  `config/import_mappings/<bank>.toml`, auto-reuse on next import from the same bank,
  `services/importer.py` applying import-time regex rules via `services/categorizer.py`.
  *Verify*: importing a sample CSV twice — second import reuses the saved mapping without
  re-prompting.

- [ ] **Phase 4 — Rule engine (bulk reclassification)**: rule CRUD UI, preview endpoint (full
  before/after diff per row; regex compile-checked at save time, never silently zero-matching),
  apply endpoint (locked write). *Verify*: invalid regex rejected at save; preview output matches
  apply output exactly.

- [ ] **Phase 5 — Reporting & visualization**: `services/aggregation.py` shared group-by layer,
  annual summary + year→month drill-down, MoM/YoY comparisons, balance/net worth chart via the
  vendored chart JS. *Verify*: aggregation unit tests against a fixture ledger with known totals;
  manual browser check of chart rendering.

- [ ] **Phase 6 — Launcher & polish**: `scripts/launch.py` (starts uvicorn bound to
  `127.0.0.1`, waits for readiness, opens browser via `webbrowser`), error-handling pass across
  invalid-regex/malformed-CSV/lock-conflict paths. *Verify*: launcher script runs end-to-end with
  no manual server start.

## Code style

All Python follows PEP 8 (enforced via `ruff check` / `ruff format`), with PEP 257-style
docstrings on modules, classes, and public functions, and type hints on all signatures. Skip
docstrings only for trivial one-liners where the signature is self-explanatory.
