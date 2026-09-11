# Personal Finance Tracker — PRD & Implementation Plan

## 1. Overview

A local, single-user web application for tracking personal finances across
multiple accounts, with category/subcategory reporting, transfers, bank CSV
import, and flexible rule-based categorization — all backed by plain text
files (CSV + TOML), no database.

**Primary user**: one person, one machine, no login.

## 2. Goals

- Track income and expenses across multiple accounts.
- Support transfers between accounts without double-counting.
- Categorize transactions (category + subcategory), both via manual setup
  and on-the-fly creation.
- Import transactions from bank CSV exports, with per-bank column mapping
  that's remembered after first setup.
- Auto-categorize on import via regex rules, and bulk-reclassify historical
  data via a separate regex rule engine (with full before/after preview).
- Report on: annual income/expense totals by category and subcategory,
  drill-down from year → month, month-over-month and year-over-year
  comparisons, account balances and net worth over time.
- Run locally with a one-click launcher — no cloud dependency, no auth.

## 3. Non-Goals (explicitly out of scope)

- Multi-user access or authentication.
- Any remote database (RDS, Postgres, MySQL, etc.) — data stays in local
  CSV/TOML files.
- Mobile/remote network access (binding beyond `localhost`).
- Real-time sync across devices.
- Automatic bank connections/APIs (Plaid, etc.) — import is manual CSV only.

## 4. Data Model

### 4.1 Accounts — `config/accounts.toml`

Each account has:
- `id` — stable short code, **never changes** even if name/description does
  (this is what transactions reference — never the account name)
- `name`
- `number`
- `description`
- `starting_balance`

### 4.2 Categories — `config/categories.toml`

- Predefined category → subcategory tree, editable by hand or via a
  settings screen.
- New categories/subcategories can also be created on the fly during
  transaction entry or import.
- No automatic dedup/cleanup — keeping the list tidy is a user
  responsibility, not the app's.

### 4.3 Ledger — `data/ledger.csv`

Single flat file, one row per transaction. Suggested columns:

```
id, date, account_id, category, subcategory, description, amount,
type (income/expense/transfer), transfer_id (nullable), notes
```

- **Transfers** are two linked rows (an outflow row in the source account,
  an inflow row in the destination account), joined by a shared
  `transfer_id`.
- **App-only writes.** The app should treat itself as the sole writer of
  this file — no manual CSV editing while the app is in use.

### 4.4 Bank import mappings — `config/import_mappings/<bank_name>.toml`

Per bank: column layout, delimiter, date format, and any bank-specific
auto-categorization regex rules. Saved after first setup, reused on
subsequent imports from the same bank.

### 4.5 Rule engine (bulk reclassification) — `config/rules.toml` or similar

- Regex-based match rules (e.g. match on `description`), reusable both at
  import time and for bulk cleanup of existing ledger rows.
- Invalid regex must fail loudly (never silently match zero rows).
- Bulk apply always requires a **preview step** first — showing a full
  before/after diff (old category → new category, and any other changed
  fields) per affected row, not just a count or bare list.

## 5. Concurrency & Data Integrity

- **File locking** on every write to `ledger.csv` (and other config files
  during edits).
- Lock file stores the writer's **PID**. On startup or before acquiring a
  lock, the app checks whether that PID is still alive; if not, the stale
  lock is cleared automatically — no manual intervention required.
- Since the app is the sole writer, referential integrity (transfer rows
  staying linked, account IDs staying valid) is enforced in application
  code, not by a database — this needs deliberate validation logic
  (e.g. a startup or on-demand ledger consistency check).

## 6. Reporting Requirements

- **Annual summary**: total income, total expense, broken down by
  category and subcategory.
- **Drill-down**: year view → click into a year → monthly breakdown.
- **Comparisons**: month-over-month and year-over-year, at the category
  and subcategory level.
- **Balances & net worth**: running balance per account (starting balance
  + all transactions to date) and combined net worth over time (line
  chart across the 5-year range).
- All reports should pull from a **shared aggregation layer** (group-by
  year/month/category/subcategory) rather than one-off logic per view.

## 7. Scale Assumptions

- ~5 years of history, ~100–150 transactions/month → roughly 7,000–9,000
  total rows.
- At this size, loading the full CSV into memory per request is
  acceptable — no indexing, pagination, or caching layer needed initially.

## 8. Tech Stack

- **Backend**: Python, FastAPI
- **Frontend**: HTMX (server-rendered, minimal JS)
- **Data**: CSV (ledger) + TOML (accounts, categories, mappings, rules) —
  no database
- **Launch**: a launcher script (e.g. shell/batch script) that starts the
  Uvicorn server and opens the browser automatically — true one-click,
  assumes Python is already installed on the machine.

## 9. Build Phases (for Claude Code)

Each phase should be buildable and testable before moving to the next.

### Phase 1 — Core ledger read/write + locking
- Define ledger CSV schema and TOML config schemas.
- Implement read/parse of ledger + accounts + categories.
- Implement write path with file locking (PID-based stale-lock recovery).
- Basic consistency check (orphaned transfer detection).

### Phase 2 — Accounts, transactions, transfers (manual entry)
- CRUD for accounts (via TOML).
- Manual transaction entry form (HTMX), including category/subcategory
  picker with on-the-fly creation.
- Transfer entry (creates linked two-row pair with shared transfer_id).
- Account balance calculation (starting balance + transactions to date).

### Phase 3 — Bank CSV import
- Upload/parse a bank CSV.
- Column mapping setup UI, saved per bank in
  `config/import_mappings/<bank>.toml`.
- Apply saved mapping on subsequent imports from the same bank.
- Apply import-time auto-categorization regex rules.

### Phase 4 — Rule engine (bulk reclassification)
- Rule definition UI (regex pattern → category/subcategory).
- Preview mode: run a rule against the ledger, show full before/after
  diff per affected row.
- Apply mode: commit the previewed changes.

### Phase 5 — Reporting & visualization
- Shared aggregation layer (group-by year/month/category/subcategory).
- Annual summary view with category/subcategory breakdown.
- Year → month drill-down.
- MoM / YoY comparison views.
- Account balance & net worth charts over time.

### Phase 6 — Launcher & polish
- One-click launcher script (start server + open browser).
- Error handling pass (invalid regex, malformed CSV import, lock
  conflicts, etc.).

## 10. Open Questions / Decisions Left to Claude Code

- Exact ledger CSV column order/naming (schema above is a starting point).
- Exact TOML schema field names for categories/rules.
- Charting library choice for balance/net worth visualizations (server-
  rendered chart images vs. a lightweight JS charting lib compatible with
  HTMX).
- Whether the consistency check (orphaned transfers) runs automatically on
  startup, on demand, or both.
