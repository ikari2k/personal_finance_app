# Implementation Plan — Personal Finance Tracker

This elaborates `finance-app-prd.md` into concrete technical decisions and a phased build order.
Phases are implemented one at a time, each its own reviewable unit of work with its own commit(s).
`CLAUDE.md` is updated after each phase lands to reflect what became concrete during that phase.

**Status**: Phases 0–6 complete, plus three out-of-sequence addenda inserted after 2.7 (transfer
editing/dialog redesign, icon-set polish, and per-category/subcategory monthly budgets — see
their entries below). All phases in this plan are now done; see `CLAUDE.md` for the substantial
further work that has continued past Phase 6 as informal, undated addenda (dashboard, bulk
transfer detection, reports drill-downs, toolbar redesigns, etc.).

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
  - `config/categories.toml`: two top-level `[income]`/`[expense]` tables, each mapping category
    name → `{icon, budget, subcategories}`, where `subcategories` maps subcategory name →
    `{icon, budget}`. `icon` is a key into the vendored icon set (`""` = unset); `budget` is a
    quoted decimal string (`""` = unset), income-only categories never carry one.
  - `config/rules.toml`: `[[rules]]` tables — `pattern, field, category, subcategory, priority`.
    Only `field="description"` is interpreted yet (Phase 3's `services.categorizer`); rule
    management (CRUD, save-time regex validation) is Phase 4.
  - `config/import_mappings/<bank_slug>.toml` (one file per bank, **revised in Phase 3**): `bank,
    delimiter, encoding, date_format, decimal_separator`, plus `columns` — ledger field name →
    **0-based column index** (not name — real bank exports can have duplicate header names) for
    `date`/`description`/`amount` (required) and `account_number` (optional, filters a
    multi-account export to the destination account). No longer has a bank-scoped `[[rules]]`
    list — rules live only in the single shared `config/rules.toml`.

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

- [x] **Phase 1 — Core ledger read/write + locking**: `storage/lock.py` (PID-based stale-lock
  recovery), `storage/ledger.py` + `storage/accounts.py` + `storage/categories.py`,
  `services/consistency.py` (orphaned transfers, invalid `account_id`). *Verify*: `uv run pytest
  tests/unit -k ledger`; stale-PID lock test (spawn+kill a process, confirm lock clears).
  - `app/models/{account,transaction,category}.py` add the pydantic domain models
    (`Account`, `Transaction`/`TransactionType`) and the `CategoryTree` type alias.
  - `app/storage/lock.py`: `file_lock(target, timeout, poll_interval)` context manager. Uses
    `os.open(..., O_CREAT | O_EXCL)` for atomic lock-file creation, clears a lock whose PID is no
    longer alive (`os.kill(pid, 0)`) before each acquisition attempt, and raises `LockError` if
    a live-owned lock isn't released before `timeout`.
  - `app/storage/ledger.py`, `accounts.py`, `categories.py` all follow the same pattern: read
    returns an empty collection if the file doesn't exist; write takes `file_lock`, then writes
    to a `.tmp` sibling and `Path.replace()`s it into place (atomic, no partial-write risk).
  - `starting_balance` is stored as a **quoted TOML string**, not a bare float — `tomli_w`
    serializes `Decimal` as a bare TOML float literal and `tomllib` reads it back as Python
    `float`, which risks silent precision loss for money. Round-tripping through `str` avoids it
    (see `app/storage/accounts.py` module docstring).
  - `app/services/consistency.py`: `check_consistency(transactions, accounts)` — pure function,
    no file I/O — flags unknown `account_id` references and transfer-type rows that aren't part
    of an exactly-two-row, zero-net `transfer_id` pair.
  - `app/main.py` now runs `check_consistency` in a `lifespan` startup hook (issues logged as
    warnings, never block startup) — the "runs automatically on startup" half of the consistency
    check invariant. The on-demand half needs a UI action and lands with a later phase's routers.
  - 15 new unit tests (`tests/unit/test_{lock,ledger,accounts,categories,consistency}.py`), all
    `tmp_path`-isolated; full suite is 19 tests, all passing. `ruff check` / `ruff format --check`
    clean.

- [x] **Phase 2 — Accounts, transactions, transfers (manual entry)**: account CRUD
  router/templates, manual transaction entry with on-the-fly category/subcategory creation,
  transfer entry (`services/transactions.py` builds the linked two-row pair),
  `services/balances.py`. *Verify*: integration test confirms both transfer rows share
  `transfer_id` with opposite-sign amounts; balance reflects `starting_balance` + rows.
  - Also add `scripts/seed_sample_data.py`: a dev-only script that populates
    `config/accounts.toml`, `config/categories.toml`, and `data/ledger.csv` with fake accounts,
    categories, and a few months of transactions (including at least one transfer pair), using
    the Phase 1 storage read/write functions. This is the first phase with a UI worth manually
    clicking through, so it's the point where seed data starts earning its keep — for browser
    smoke-testing account balances and transaction entry once those views exist.
  - The script itself is checked into git (it's code); the `data/`/`config/` files it generates
    stay gitignored, same as real user data — never commit generated sample data alongside real
    financial data conventions.
  - Rerunning the script should be safe (overwrite, not append/duplicate) so it stays useful as a
    "reset to a known state" tool through the rest of development.
  - **Storage layer became testable via routers**: `app/storage/{ledger,accounts,categories}.py`
    read/write functions used to default their `path` argument to a value frozen at import time
    (`path: Path = LEDGER_PATH`), which meant nothing could redirect a router's storage calls
    away from the real `data/`/`config/` dirs during a test. Changed all three to
    `path: Path | None = None` and resolve `config.LEDGER_PATH` (etc.) *at call time* instead —
    now `tests/integration/conftest.py`'s `client` fixture just monkeypatches the three
    `app.config` path attributes once, and every router transitively picks up the redirect.
  - `app/templating.py` holds the single shared `Jinja2Templates` instance (moved out of
    `app.main`) so routers can import it without a circular import back to `main`.
  - **htmx is vendored**, not loaded from a CDN: `app/static/htmx.min.js` (htmx 2.0.10, fetched
    once during development) — consistent with the "app must run offline" rule already in
    CLAUDE.md, which previously only covered the Phase 5 charting library.
  - New service modules follow the Phase 1 `services/consistency.py` pattern — pure functions
    over in-memory data, no file I/O: `app/services/accounts.py` (`add_account`,
    `update_account`, `remove_account` — enforces unique IDs, immutable `id`, and refuses to
    delete an account still referenced by a transaction), `app/services/balances.py`
    (`account_balance`, `all_balances`), and `app/services/transactions.py` (`new_transaction`
    normalizes user-entered unsigned amounts to signed by `type`; `new_transfer_pair` builds the
    linked two-row pair; `ensure_category` implements on-the-fly category/subcategory creation).
  - Routers (`app/routers/{accounts,transactions,transfers}.py`) stay thin: read via storage,
    call a service function to validate/build, write via storage, re-render an HTMX table/form
    fragment. Validation errors (duplicate account id, unknown `account_id`, non-positive
    amount, same-account transfer, delete-with-transactions) are caught and re-rendered inline
    rather than raising HTTP 500s.
  - 36 new tests (17 service unit tests, 19 router integration tests via a `tmp_path`-redirected
    `TestClient`); full suite is 55 tests, all passing. `ruff check` / `ruff format --check`
    clean. Also manually verified live via `scripts/seed_sample_data.py` + `curl` against a real
    `uvicorn` process: balances, transaction entry, transfer creation, and the duplicate-account
    error path all behaved correctly before the server was stopped and sample data reset.
  - **Post-Phase-2 UI refinements** (still within Phase 2 scope, done as follow-up polish before
    starting Phase 3):
    - Vendored **Pico.css 2.1.1** (classless) and a **JetBrains Mono** variable font — see
      `CLAUDE.md`'s "UI conventions" section for the full styling/dialog convention writeup.
    - Converted **every form to a native `<dialog>` modal** (Add/Edit account, Add income, Add
      expense, Record transfer) — no more inline-swap-below-the-button forms anywhere. Standard
      pattern: `HX-Trigger: close-dialog` response header closes the dialog on success, an
      out-of-band table swap refreshes whatever table is actually on the page (which may belong
      to a different router — see `transfers.py` reusing `transactions.render_table`), and a
      validation error re-renders the form in place with the user's input preserved.
    - **Split category storage into separate income/expense trees**: `categories.toml` now has
      top-level `[income]`/`[expense]` tables instead of one flat tree (`app.models.category
      .CategoriesByType`, superseding the flat `CategoryTree` used since Phase 1).
      `services.transactions.ensure_category` takes a `TransactionType` and updates the matching
      bucket only. "Add income" and "Add expense" are now separate buttons/routes
      (`GET /transactions/new/{income,expense}`) each showing only that type's categories, rather
      than one form with a type dropdown.
    - Trigger buttons are color-coded: `.btn-income` (green), `.btn-expense` (red), `.btn-transfer`
      (amber) in `app/static/style.css`.
    - **Transactions list redesigned as month-then-type groups** (explored as 3 design-canvas
      propositions; "month-first, type nested" was chosen). Adds `app/services/aggregation.py`
      ahead of Phase 5, which will extend it with category/subcategory grouping rather than get a
      second group-by module. Collapsible `<details>` per month (Pico styles these as an
      accordion for free), color-coded Income/Expense/Transfer sub-blocks with subtotals, a net
      total per month (excludes transfers — see the module docstring for why the transfer
      subtotal is volume-moved, not a signed sum: it's always zero otherwise).
    - **Both grouping dimensions became independently toggleable** after user feedback that
      grouping should be optional, not fixed: `by_month`/`by_type` query params on
      `GET /transactions`, backed by `aggregation.grouped_transaction_view` (always computes the
      full breakdown, then collapses whichever dimension is off via `merge_months` / clearing
      `.groups` — one code path, not four). A pure-CSS "just hide the type headers" approach was
      tried first and rejected: it can't fix the underlying sort order when type grouping is off
      (transactions need to actually interleave chronologically, not just lose their section
      labels), so this is a real HTMX round-trip, not a client-side filter. Known simplification:
      the OOB refresh after creating a transaction/transfer always resets to the default (both
      on) rather than threading the page's current toggle through those unrelated POST flows.
    - **Toolbar decluttered (round 1)** after user feedback that the original 8-button toolbar (3
      entry buttons + expand-all/collapse-all + 3 type-visibility filter chips) was too busy. The
      type-visibility chips were dropped entirely — the "type grouping: on/off" toggle covers the
      same underlying need. Expand-all/collapse-all now only render when month grouping is on.
      The month label (`<summary>`) and the flat-mode net-total line are both styled
      larger/bolder (`.month-label` / `.flat-net-total`) so they read as a clear top-level heading
      rather than blending into the rows below.
    - **Toolbar decluttered (round 2)**: further feedback that the CTA buttons' *placement* still
      felt wrong even after round 1. Previewed the redesign as a static artifact (built from the
      real vendored `pico.min.css` + `style.css`, so it was a faithful preview) before touching
      real templates. Landed on: `<h1>` + the 3 colored entry CTAs share one `.page-header-row`
      (`position: sticky`), shrunk slightly so they read as page actions, not a wall of buttons;
      everything about *how you view the data* — grouping toggles, expand/collapse, and the new
      account filter — moved into `.view-options`, a visually distinct, muted, **non-sticky**
      band inside `_table.html` (a deliberate trade-off: it scrolls away, but the primary
      entry-point buttons never do).
    - **Account filter** added (`account_id` query param, same pattern as `by_month`/`by_type`):
      filters the ledger *before* aggregating, so subtotals reflect only the selected account.
      All three controls (account select, month toggle, type toggle) round-trip all three params
      every time, so changing one never resets the others. The row-level Account column hides
      itself when filtered to one account, since it'd otherwise show the same name on every row.
    - **Fixed a real CSS bug found while building the redesign preview**: monthly net totals
      didn't align with each other. Cause: Pico's `summary::after` chevron becomes a third flex
      item inside a `display: flex` `<summary>`, so `justify-content: space-between` put the net
      total in the middle slot, not flush right, at a position that shifted with each month's
      label width. Fixed with `display: grid; grid-template-columns: 1fr auto auto` instead.
    - **Category/subcategory filters + toolbar decluttered (round 3)**, much later: added
      `category`/`subcategory` ledger filters (same `account_id`-style pattern — query params
      falling back to sticky cookies, filtering before aggregation, AND-combined). The resulting
      3-select-plus-4-button toolbar was reported as noisy, so it was redesigned to 4 controls:
      Account, one combined Category select (subcategories nested per category via `<optgroup>`
      rather than a separate top-level Subcategory select — a category's subcategories are already
      implied by which one is picked), a "Group by" select replacing the two grouping-toggle
      buttons (its four options are exactly the four `by_month`/`by_type` combinations), and one
      Expand/collapse-all button (its onclick checks whether any `.month-section` is currently
      closed to decide which way to toggle, instead of two direction-specific buttons). Since a
      single `<select>` can only submit one value under one `name=`, the combined Category and
      Group-by selects use htmx's dynamic `hx-vals='js:{...}'` form to also read a second value off
      a `data-*` attribute on the just-selected `<option>` — the one exception to the otherwise
      fully-declarative htmx pattern used everywhere else, still with no separate `<script>`.

- [x] **Phase 2.5 — Categories management**: a dedicated `/categories` page for the income and
  expense trees the app has been building on-the-fly since Phase 2 — the "editable by hand or via
  a settings screen" half of the PRD's categories requirement (§4.2). Inserted here (not
  renumbered into Phase 3+) so the phase numbers already referenced elsewhere (this doc,
  `CLAUDE.md`, code comments) don't need churning. Scope grew beyond the original plan, at
  explicit request, to add a per-category/per-subcategory **icon** — see below.
  - Add category (to the income or expense tree), add subcategory (to an existing category),
    rename either, delete either. Reuses the established `<dialog>` pattern exactly (open via
    `hx-get`, close via the generic `close-dialog` event, OOB-refresh the tree on success,
    re-render the form in place with input preserved on error) — one shared
    `categories/_form.html` serves both category and subcategory add/edit (parametrized by
    `action_url`/`dialog_id`/`kind`, the same shape as `transactions/_form.html`'s
    income/expense parametrization), and delete reuses the app-wide `hx-confirm` → shared
    `#confirm-dialog` mechanism with zero new wiring.
  - **Deletion is not guarded by ledger usage, unlike account deletion.** A ledger row's
    `category`/`subcategory` are free-text strings copied in at entry time (see
    `services.transactions.ensure_category`) — not a foreign key the consistency checker
    validates, the way `account_id` is. Deleting or renaming a category in the tree does **not**
    touch existing ledger rows and is never blocked by their existing use, consistent with the
    PRD's "no automatic dedup/cleanup — keeping the list tidy is a user responsibility" stance
    (§4.2). `services/categories.py`'s module docstring calls this out explicitly so it isn't
    "fixed" by analogy with `services.accounts.remove_account` later.
  - **Icon feature** (added mid-phase, after design review — see the two published mockup
    artifacts from that review for the rejected/considered alternatives): each category and
    subcategory optionally carries an icon key from a vendored 42-icon stroke set
    (`app/templates/_category_icons.html`, one Jinja macro per icon plus a `category_icon(key)`
    dispatcher; `app.models.category.VALID_ICONS` is the source of truth for which keys are
    valid, checked in `services.categories` — the template file must keep a macro for every key
    there). New categories/subcategories (including ones created on-the-fly during transaction
    entry, via `ensure_category`) start with no icon (`""`, never `None` — TOML has no null,
    matching this app's existing convention of `""` for "unset" elsewhere) until assigned here.
    The picker is a plain radio-input grid (`categories/_form.html`'s `.icon-grid`), selected
    state styled via `:has(input:checked)` — no JS needed. Rejected alternatives, in order:
    free-typed emoji (OS-native picker, zero build cost, but renders inconsistently across
    platforms and the user wanted a specific look); a curated ~24-icon hand-drawn set (too
    narrow once the user's actual reference — a native app's category list — showed the real
    breadth needed: specific transport modes, insurance, refunds, utilities, etc.); settled on
    hand-drawing a **larger** (42-icon) set in the same visual language as the existing
    edit/delete row glyphs, after two rounds of "which of these do you actually need" against
    real reference screenshots, rather than adopting a full third-party icon library.
  - **Schema change**: `config/categories.toml` moved from `Category = ["Sub", ...]` (bare list)
    to `[type.Category] icon = "..."` + `[type.Category.subcategories] Sub = "..."` (nested
    tables, name → icon). `app.storage.categories.read_categories` transparently upgrades the
    old bare-list shape in memory on read (`_normalize_entry`) — no separate migration script —
    and the next `write_categories` call persists the upgraded shape. Confirmed against the
    user's actual pre-existing `config/categories.toml` in the old format: read, edited via the
    UI, and correctly rewritten in the new format with no data loss.
  - Transactions list rows now show each category's/subcategory's icon inline (`_table.html`'s
    `txn_row` macro takes `categories` as a fourth arg to look the icon up by
    `txn.type.value`/`txn.category`/`txn.subcategory`), colored to match the existing
    income/expense/transfer color scheme (`.cat-cell-icon-{income,expense,transfer}` — a class
    per row rather than relying on `.type-group[data-type=…]` ancestor styling, since a flat
    (type-grouping-off) list has no such ancestor). The category/subcategory `<datalist>`
    autocomplete in the transaction entry form still shows plain text — native `<datalist>`
    can't render an icon next to an option, so icons only ever appear in the categories page and
    the transactions list, not the entry form.
  - *Verified*: unit tests for every `services.categories` function (add/rename/delete category
    and subcategory, icon validation, no-usage-guard) in
    `tests/unit/test_service_categories.py`; the old-format-upgrade shim in
    `tests/unit/test_categories.py`; router integration tests including that deleting a category
    with existing ledger rows referencing it succeeds anyway
    (`tests/integration/test_categories_router.py`); a live smoke test against the running app
    covering add/edit/rename/delete for both categories and subcategories, plus the user's own
    concurrent live use of the same running instance during development (edited real categories
    through the UI, confirming the schema upgrade and icon assignment both round-tripped
    correctly).

- [x] **Post-Phase-2.5 addendum — expand/collapse-all, sticky header**: the `/categories` page
  gained the same single-button expand/collapse-all toggle already used on the transactions list
  (`.cat-row` in place of `.month-section` — its onclick checks whether any target `<details>` is
  currently closed to decide which way to toggle, rather than two separate buttons), placed in a
  new sticky `.page-header-row` alongside the `<h1>` — reusing that exact convention from the
  transactions page rather than inventing a new header treatment, so it stays visible while
  scrolling a long category list. *Verified*: an integration test confirming the button and sticky
  header render; full suite 352 passing; a live check confirmed the toggle actually opens/closes
  every category's `<details>` together.

- [x] **Post-Phase-2.5 addendum #2 — renames propagate into rules**: renaming a category or
  subcategory now also updates every matching rule in `config/rules.toml`, so an existing
  auto-categorization rule keeps pointing at the new name instead of silently recreating the old
  one on the fly (via `services.transactions.ensure_category`) the next time it fires. This is a
  deliberate, narrow exception to CLAUDE.md's established "renaming a category does not touch
  existing ledger rows, no automatic dedup/cleanup" stance (§4.2, Phase 2.5's own write-up above)
  — that invariant is specifically about the *ledger* (free-text copies, not a foreign key); rules
  are different; a rule left pointing at a stale name is a real, ongoing behavioral bug, not just
  a cosmetic mismatch. Ledger rows themselves are still never touched by a rename (unchanged).
  `services.categorizer.rename_category_in_rules`/`rename_subcategory_in_rules` are pure
  functions the two update routes call after the category/subcategory rename itself succeeds
  (only when the name actually changed, not on an icon/budget-only edit); the subcategory rename
  is scoped to its parent category, since the same subcategory name can exist under different
  categories (renaming "Other" under "Groceries" must not touch an unrelated "Other" rule under
  "Shopping"). The success toast mentions how many rules were updated, e.g. "Category updated (2
  rules updated)" — silent when zero, so the common no-rules-affected case doesn't call attention
  to itself. *Verified*: unit tests for both rename functions (renames matching rules, scoped
  correctly, no-op when names match, doesn't mutate input) and integration tests for both routes
  (renames rules, leaves unrelated ones alone, no rule touched on a non-rename edit); full suite
  360 passing; a live end-to-end check confirmed both a category rename and a subsequent
  subcategory rename correctly propagated into a real rule.

- [x] **Phase 2.6 — Edit/delete transactions**: transactions were create-only since Phase 2; this
  closes that gap. Same decimal-insertion reasoning as 2.5 — slots after it without renumbering
  Phase 3+.
  - Each non-transfer row gets an Edit glyph opening the same dialog pattern as "Add
    income"/"Add expense" (`GET /transactions/{id}/edit`, prefilled), posting to
    `POST /transactions/{id}`. `services.transactions.update_transaction` shares its
    validation (`_signed_amount`) with `new_transaction` but keeps the original `id` instead of
    minting a new one, and rejects editing a row that's currently a transfer leg.
  - **Transfers are never edited in place — only deleted, as a pair.**
    `services.transactions.remove_transaction` deletes a plain row by `id`, but for a row with a
    `transfer_id` it removes *every* row sharing that `transfer_id` (i.e. both legs) in the same
    call — never just the one clicked. This is the same invariant as transfer creation
    (`CLAUDE.md`), just for the deletion direction: one leg can never be edited or deleted
    without the other, since either would desync the pair or orphan a leg — exactly what
    `services.consistency.check_consistency` flags. To change a transfer's amount/date/accounts,
    delete it and record a new one via "Record transfer".
  - **Row-action design, generalized beyond this phase**: Edit/Delete render as stroke-SVG glyphs
    (`app/templates/_icons.html` — shared, not per-table: blue pencil, red trash), hidden via
    `opacity: 0` until the row is hovered *or* has keyboard focus inside it
    (`tr:hover`/`tr:focus-within .row-actions`). They float `position: absolute; left: -4.25rem`
    outside the table entirely (icons live inside the row's first `<td>`, not a trailing actions
    column) rather than sitting in a normal trailing table cell. That's a deliberate fix, not
    just a style choice: in `transactions/_table.html` each type-group renders its own separate
    `<table>`, so a trailing actions cell was sized differently per table (1 icon for a transfer
    leg's row, 2 for income/expense), which made the Amount column land at a different
    x-position depending on which sub-table a row belonged to. Taking the icons out of the
    column flow entirely fixes that as a side effect — column count/width no longer depends on
    how many action icons a row has.
    - **Went through two iterations on where "outside the table" actually floats to.** The first
      cut carved out a dedicated gutter via `margin-left` on the `<table>` itself — this worked,
      but left a permanent empty band down the left of every table even when nothing was
      hovered, which looked wrong on its own terms once seen live. Reverted: the table now sits
      at its natural, unindented position (flush with the rest of the page), and the icons float
      further out still, into the page's own ambient side margin, so nothing is reserved or
      visible while idle. Trade-off accepted deliberately: on a narrow browser window (little
      ambient margin to begin with) the icons can sit close to, or past, the viewport edge —
      judged acceptable for a desktop-only local tool. Don't reintroduce the `margin-left` gutter
      to make this "safer"; that re-creates the exact look that got rejected.
    - Row padding (`padding-top`/`padding-bottom: 0.6rem` on these tables' `td`s) is still needed
      regardless of gutter approach, so a vertically-centered 2rem icon button doesn't spill into
      the row above/below when revealed — tuned by eye against a live screenshot, not derived up
      front; re-check it if icon size changes.
    Applied to the accounts table too (previously plain "Edit"/"Delete" text). **Use this same
    convention for every future table where a row can be edited/deleted** — e.g. the Phase 2.5
    categories list — rather than introducing a different affordance.
  - *Verify*: editing an income/expense row's amount or category updates the account balance and
    category tree correctly; deleting a transfer removes both legs in the same write, and a
    startup/on-demand consistency check afterward finds no orphaned-transfer issue. Verified live
    against the real dev server (edit, delete-plain-row, delete-transfer-pair) as well as via
    unit/integration tests.

- [x] **Phase 2.7 — Account view improvements**: three related account/transactions-view
  usability gaps noticed while using the app day to day. Same decimal-insertion reasoning as 2.5
  and 2.6.
  - **`starting_balance` now shows on the accounts list** as its own column, between
    Description and the current Balance column — previously the only place it was visible at all
    was the edit-account dialog.
  - **Clicking an account row navigates to its filtered transactions** (`/transactions?
    account_id=<id>`) — broader than the original "click the account name" plan, the whole `<tr>`
    is clickable (`accounts/_table.html`, an `onclick` on the row rather than wrapping cell text
    in an `<a>`), since Phase 2's account filter already did the filtering and this is pure UI
    wiring. The row's `onclick` bails out via `event.target.closest('.row-actions')` when the
    click landed on the Edit/Delete glyphs, so those keep working without triggering a
    navigation.
  - **Month/type grouping, and the account filter, now persist across page navigation** via a
    `by_month`/`by_type`/`account_id` cookie triple (`app/routers/transactions.py`).
    `GET /transactions` takes all three as optional (`bool | None` / `str | None`, default
    `None`); a value of `None` (query param genuinely absent — a plain nav link) falls back to
    the cookie, while an explicit value (including explicit `account_id=`, e.g. picking "All
    accounts") always wins and is never overridden by a stale cookie. This is why `account_id`
    needed `str | None` rather than defaulting to `""` — `""` is itself a meaningful explicit
    choice, so only `None` means "fall back". Every request re-writes all three cookies to the
    resolved value, so cookie and last-shown state can't drift apart. `render_table()` (the
    post-create/transfer OOB refresh, shared with `app.routers.transfers`) reads the same three
    cookies instead of hardcoding "both groupings on, no filter" — resolving the "known
    simplification" noted under Phase 2's addendum, as a side effect rather than a separate task.
    **Revised mid-phase**: the account filter was originally planned as deliberately *not*
    persisted (reasoning: "you look at one account, then probably want 'all accounts' again next
    time you arrive fresh") but was changed to persist, at explicit request, once built — don't
    re-introduce the "per-visit, not sticky" framing without checking whether that's still
    wanted.
  - **Gotcha hit while wiring the cookies**: `Response.set_cookie()` called on a `Response`
    injected via FastAPI's dependency mechanism is silently discarded if the route also returns
    an explicit `Response` (a `TemplateResponse`, here) — FastAPI only merges that injected
    object's cookies/headers into the final response when the endpoint returns plain data (a
    dict/model) for FastAPI itself to wrap. Fixed by building the `TemplateResponse` into a
    variable and calling `.set_cookie()` on *that* object before returning it, and dropping the
    now-useless injected `Response` parameter. Apply the same pattern for any future
    cookie-setting route that returns a `Response` directly.
  - **Amount-column alignment across type-groups, fixed as a follow-on from this phase's live
    testing** (not in the original plan, but the same "account/transactions view" surface): each
    type-group (Income/Expense/Transfer) renders its own `<table>` (see CLAUDE.md), and left at
    the default `table-layout: auto` each one sized its columns independently from its own
    content — the Amount column landed at a different x-position per sub-table, and the
    type-group header's subtotal didn't line up with its own rows either. Fixed with
    `table-layout: fixed` plus a shared `<colgroup>` (`txn_colgroup` macro in
    `transactions/_table.html`) so every table on the page uses identical column widths
    regardless of its own content, and `text-align: right` on the last cell. Date and Amount get
    a `calc(10ch + 2rem)` width (their content is fixed-format and fully predictable — 2rem
    because `table-layout: fixed` subtracts a cell's own padding from its `<col>` width to get
    the content box, and Pico's `td` padding is `1rem` each side); Account/Category get generous
    but not unbounded percentages (24%/26%) tuned against live content so typical values fit on
    one line, accepting that the occasional unusually long category/subcategory combination (or
    description) still wraps — the same trade-off already accepted for free-text fields
    elsewhere, not a regression to chase to zero.
  - *Verified*: starting balance visible per account; clicking an account row (but not its
    Edit/Delete icons) lands on its filtered transaction list; toggling a grouping or the account
    filter, then navigating to `/accounts` and back to `/transactions` via the nav link (not the
    browser back button), preserves both choices; Amount column and type-group subtotals line up
    across Income/Expense/Transfer sub-tables and across months — all via integration tests
    (`tests/integration/test_transactions_router.py`,
    `tests/integration/test_accounts_router.py`) and repeated live smoke tests (including
    pixel-level measurement of column edges) against the running app.

- [x] **Post-2.7 addendum — Transfer editing, and dialog-form redesign for income/expense/
  transfer**: two related requests that came in once 2.7 shipped; not part of the original phased
  plan, recorded here rather than folded into an existing phase's checklist.
  - **The income/expense/transfer dialogs now use the same compact 2-column grid + outlined
    Cancel/primary Save footer the account form already used** (`.form-grid-2`/`.dialog-footer`
    in `app/static/style.css` — reused as-is, not reimplemented). Transaction form pairs:
    Account+Date, Category+Subcategory, Description+Amount, Notes full-width. Transfer form
    pairs: From+To account, Date+Amount, Description+Notes.
  - **Dialog titles are now generic** ("Income", "Expense", "Transfer" — matching the account
    dialog's plain "Account") instead of the previous action-specific "Add income"/"Add
    expense"/"Record transfer", which read wrong once editing reused the same dialog and showed a
    "Save changes" button under an "Add expense" heading. Reuse the generic-title convention for
    any future dialog rather than re-introducing an action-specific one that goes stale on edit.
  - **Transfers can now be edited in place** — `app.services.transactions.update_transfer_pair`
    rebuilds both legs from the submitted field values and swaps them in together (never one
    without the other, preserving the invariant in CLAUDE.md's "Key invariants" section), keeping
    each leg's original `id`. `update_transaction` still refuses to touch a transfer leg directly
    (single-row edits can't safely do this) and its error message now points callers at
    `update_transfer_pair` instead of "delete and re-record" — that phrasing is gone from the
    code, though deleting and re-recording still works fine as an alternative, it's just no
    longer the *only* option.
  - **Router/template wiring**: `GET /transfers/{transfer_id}/edit` (prefilled from whichever leg
    is the outflow vs. inflow, by amount sign) and `POST /transfers/{transfer_id}` (mirrors
    `create_transfer`'s validation/error-rendering shape) in `app/routers/transfers.py`.
    `transactions/_table.html`'s row-actions Edit icon is no longer conditionally hidden for
    transfer rows — it now branches its `hx-get` URL by type (`/transfers/{transfer_id}/edit` vs
    `/transactions/{id}/edit`) but always targets `#{{ txn.type.value }}-dialog-content`, which
    already resolves to the right dialog for all three types without extra plumbing.
  - *Verified*: unit tests for `update_transfer_pair` (both legs replaced keeping ids, unknown
    transfer id / unknown account / same account / non-positive amount all rejected) in
    `tests/unit/test_service_transactions.py`; integration tests for the edit-form prefill and
    the update route (including that a rejected edit leaves the ledger unchanged) in
    `tests/integration/test_transfers_router.py`; live smoke test confirming the dialog title,
    "Save changes" label, dialog close, toast, and updated row all behave correctly end to end.

- [x] **Post-2.7 addendum — Icon-set polish**: several rounds of feedback against the live Phase
  2.5 icon feature, not part of the original phased plan.
  - **Uniform sizing via shared CSS custom properties**: `--category-icon-size` (22px) and
    `--subcategory-icon-size` (18px), defined once in `app/static/style.css`'s `:root` and
    referenced by every place a glyph renders (categories-page category/subcategory rows, the
    icon-picker grid, the transactions-list inline icon) — previously each of these rendered at
    its own independently-tuned size, which read as inconsistent once compared side by side.
  - **Colored by transaction type** in the transactions list (`.cat-cell-icon-{income,expense,
    transfer}`, green/red/amber) rather than a single neutral color everywhere.
  - **Per-icon tooltip hints**: `app.models.category.ICON_HINTS` (a short "what's this for" string
    per icon key), rendered as each icon-picker choice's `title` attribute; kept in sync with
    `VALID_ICONS` via a dedicated test asserting the two sets match exactly
    (`tests/unit/test_categories_icon_data.py`).
  - **Subcategory-count pill** on each category row (`.sub-count-pill`) — always rendered, even at
    zero, and hidden via `visibility: hidden` rather than not rendered at all, so the expand
    chevron (Pico's auto-appended `summary::after`) stays at the same x-position across every row
    regardless of whether a given row's pill is visible. Reused the same
    always-render-hide-when-empty technique later for the budget pill (see below).
  - **Icon set grew from 42 to 44 keys**: added `fuel`/`parking` (`app/templates
    /_category_icons.html`) for car-related subcategories, alongside redraws of `plane` (a
    proper paper-plane/send silhouette, replacing a plain triangle dart), `bank`, `car`, and
    `gamepad` for more visual detail, all against reference screenshots of a native app's category
    icons.
  - **Categories-list Category column no longer wraps unnecessarily** in the filtered
    single-account transactions view: `txn_colgroup(show_account)`
    (`transactions/_table.html`) previously hardcoded Category's width to 26% regardless of
    whether the Account column was shown; when Account is hidden, that freed-up width was going
    entirely to the flexible Description column instead of also benefiting Category. Fixed by
    making Category's width conditional (26% with Account shown, 42% without).
  - *Verified*: full test suite green throughout; live smoke-tested via headless Playwright
    against the running dev server (icon grid, uniform sizing, tooltips, pill visibility, column
    widths), with `data/`/`config/` backed up and restored around every mutating test so the
    user's own concurrent live session was never disturbed.

- [x] **Post-2.7 addendum — Per-category/subcategory monthly budgets, and related fixes**: the
  first slice of budget tracking (Phase 5 will add the actual monthly spend-vs-budget report;
  this is just the data model and its entry UI), plus a few small UI fixes noticed alongside it.
  Not part of the original phased plan.
  - **Icon picker's "no icon" option is now a dedicated "Clear icon" button** above the grid
    (`categories/_form.html`), not a `–` tile living inside the grid itself — the tile read as
    "an icon called dash", which was the actual complaint. The button just unchecks every
    `input[name=icon]` radio via a small inline `onclick` (native radio groups can't be
    deselected without JS); the router already defaulted `icon: str = Form("")` for "no radio
    checked", so no backend change was needed.
  - **Schema**: `CategoryEntry` gained a `budget: str` field, and subcategories moved from a bare
    icon string to their own `SubcategoryEntry` TypedDict (`{icon, budget}`) — a second schema
    migration on top of Phase 2.5's, with the same shape of backward-compat shim
    (`app.storage.categories._normalize_subcategory` upgrades a bare icon string in memory on
    read; `_normalize_entry` extended to fill in a missing `budget` key). `""` means "unset",
    same convention as `icon`. **Income categories/subcategories can never have a budget** —
    rejected in `services.categories` (`_budget_str` raises if `txn_type is
    TransactionType.INCOME` and a budget is given) and the form field is hidden client-side for
    income (`show_budget` flag threaded through `_render_form`) — a budget exists to flag
    overspending, which isn't a meaningful concept for income.
  - **Category and subcategory budgets are independent thresholds, not a hierarchy** — a
    category's budget is checked against total spend across *all* its transactions (every
    subcategory plus any transaction with no subcategory); each subcategory's budget is checked
    only against its own spend. Deliberately **not** validated against each other at save time
    (a subcategory's budgets summing to more than the category's own is a legitimate,
    "optimistic" setup, not an error) — instead, `services.categories
    .subcategories_exceed_category_budget` flags this combination for **display only**, surfaced
    on the categories list as an amber pill plus an explicit warning-triangle glyph
    (`_icons.html`'s `warning_icon()`) — color alone (an amber pill next to other pills) didn't
    read as "alert" clearly enough on its own.
  - **Configured budgets show inline on the categories list** as a pill next to the name
    (`.budget-pill`, always rendered for category rows and hidden via `visibility: hidden` when
    unset, for the same chevron-alignment reason as the subcategory-count pill; conditionally
    rendered for subcategory rows, which aren't a shared grid). This is just the configured
    amount, not a spend-vs-actual comparison — that's still a future Monthly Budgets page.
  - **Categories-page row-actions overlap, fixed**: the shared hover-reveal `.row-actions` pattern
    (Phase 2.6) floats left into the page's own ambient margin, which works for a single table but
    broke down for the Income/Expense side-by-side layout — the Expense column's rows floated
    left *past* the 3rem gap between columns (smaller than `.row-actions`' 4.25rem offset) and
    into Income's own content. Fixed by mirroring the Expense column's offset to the right
    (`right: -4.25rem`) instead, into the page's own right margin; reset back to `left` under the
    existing `max-width: 700px` breakpoint where the two columns stack into one.
  - **Transactions list shows only the subcategory, not "Category / Subcategory", when a
    subcategory is set** (`transactions/_table.html`'s `txn_row` macro) — confirmed the entry
    form still supports both category-only and category+subcategory expenses end to end
    (`subcategory` was already an optional form field; unaffected by this display change).
  - **`scripts/seed_sample_data.py` updated** to the current icon-and-budget-bearing schema
    (previously still built the old bare-list shape, relying on the read-side upgrade shim to
    paper over it), given realistic per-category/subcategory icons from the current 44-icon set,
    and given realistic monthly budgets chosen specifically to demonstrate every
    category/subcategory-budget combination discussed for this feature side by side: some
    categories with every subcategory budgeted and comfortably under the category total
    (Housing, Entertainment), some with only a few subcategories budgeted (Groceries, Shopping),
    one with a category budget and no subcategory budgets at all (Health), and one deliberate
    exception — Transportation's Fuel (60) + Public Transit (80) budgets add up to more than its
    own (100) — to exercise the "exceeds" warning pill on a fresh seed without any manual setup.
  - *Verified*: unit tests for every new/changed `services.categories` function (budget
    acceptance/rejection per type, negative-budget rejection, the exceeds-budget flag in all four
    independent/dependent combinations) and the storage-layer upgrade shim
    (`tests/unit/test_categories.py`, `tests/unit/test_service_categories.py`); integration tests
    for the income-budget rejection, the show/hide budget field per type, and the warning pill
    appearing/not-appearing (`tests/integration/test_categories_router.py`); live smoke test
    confirming the warning pill+icon against the user's own real category data (which, by
    coincidence, already had a category over its subcategory-budget sum) and again after
    re-running the updated seed script; `data/`/`config/` backed up and restored around every
    mutating live test (except the final seed-script run, which intentionally resets them).

- [x] **Phase 3 — Bank CSV import**: a three-step `/import` wizard (upload → one-time mapping
  setup for a new bank → preview/confirm), designed directly against a real, messy sample export
  (a 101-column Credit Agricole CSV) rather than a synthetic format, which surfaced several real
  requirements the original plan wording didn't anticipate.
  - **Mapping keyed by column index, not name**: the sample file has duplicate header names
    (two columns both called "Kwota") — a name can't reliably identify a column, so
    `app.models.import_mapping.ImportMapping.columns` maps a ledger field to a 0-based index
    instead. The mapping-setup UI still shows each column's header text and a sample value in a
    picker, so the index a user ends up choosing is still made from readable information.
  - **`ImportMapping` also carries `encoding` and `decimal_separator`**, beyond the originally
    planned `delimiter`/`date_format`/`columns`: the sample file is Windows-1250, not UTF-8 (a
    decode failure is caught and shown as an inline error, not a crash), and amounts are
    European-formatted with a currency suffix (`"-5,99 PLN"`). `services.importer.parse_amount`
    strips everything except digits/`-`/the configured separator before parsing, which handles
    the currency suffix and any thousands-separator character generically without a dedicated
    "strip currency symbol" setting.
  - **An optional `account_number` mapping column filters a multi-account export**: the sample
    file interleaves two of the bank's own accounts (a checking account and its linked credit
    card) in one export. When mapped, `services.importer.filter_by_account_number` keeps only
    rows whose value there matches the destination `Account.number` (whitespace/case-insensitive),
    and the preview shows a "N rows filtered out (different account)" count rather than silently
    misattributing them.
  - **Duplicate detection before writing**: `services.importer.find_duplicates` matches a parsed
    row against existing ledger rows on the same account by date + amount + description
    (case/whitespace-insensitive) — the closest a CSV row (no natural id) has to a stable
    identity — so re-importing the same or an overlapping statement shows "N duplicates skipped"
    instead of doubling every transaction. Verified live: importing the same real file twice
    showed 132 new/0 duplicates the first time, 0 new/132 duplicates the second.
  - **Imported rows are always income or expense, never a transfer**, and amount sign maps
    directly to expense/income with no flipping (unlike manual entry, which takes an unsigned
    magnitude plus an explicit type) — see `services.importer`'s module docstring for why
    transfer-pairing isn't attempted from a CSV.
  - **Import-time auto-categorization, minimally**: `services/categorizer.py` matches a row's
    description against `config/rules.toml` (`field="description"` only, for now), defaulting to
    a "Uncategorized" category (auto-created via the existing `ensure_category`, same mechanism
    manual entry already uses) when nothing matches or no rules exist yet — full rule
    CRUD/management is Phase 4; Phase 3 only needed to *apply* whatever's already in the file. A
    bad regex raises loudly (`services.categorizer._compile`), per CLAUDE.md's invariant.
  - **No server-side session state**: the uploaded file's bytes are threaded across the wizard's
    steps as a base64 hidden form field, matching the rest of the app's stateless-per-request
    design, rather than a server-side temp file — the whole page (`#import-wizard`) is swapped
    step to step via htmx.
  - *Verified*: unit tests for every `services.importer`/`services.categorizer` function
    (amount/date parsing including the European format, account-number filtering, duplicate
    detection, rule matching and priority ordering, invalid-regex rejection) and the
    one-file-per-bank storage layer (`tests/unit/test_importer.py`,
    `tests/unit/test_categorizer.py`, `tests/unit/test_import_mappings_storage.py`,
    `tests/unit/test_rules_storage.py`); integration tests for the full wizard flow, mapping
    reuse on a second upload, duplicate flagging, and rule application
    (`tests/integration/test_import_router.py`); and a full live run through the actual browser
    against the real 134-row Credit Agricole export — decode-error handling, delimiter/encoding
    correction, the column picker, the multi-account filter (132 kept / 2 filtered), the preview,
    confirm, and the second-import dedup — with `data/`/`config/` backed up and restored
    afterward via a throwaway test account, never touching the user's real data.

- [x] **Post-Phase 3 addendum — real-usage fixes and follow-ons**: the moment the wizard was
  used against a real bank account (not just the earlier sample-file testing), several rounds of
  real friction and one real data-mismatch surfaced, all fixed in the same session as they came
  up. Not part of the original phased plan.
  - **Mapping-setup validation moved before persistence**: a bad setting (wrong `date_format`,
    wrong column) used to 500 *and* still get written by `write_mapping`, so every later import
    from that bank silently reused the same broken mapping with no way back into setup. Now
    `save_mapping_setup` parses the file with the candidate mapping first; on failure it
    re-renders the setup form with the error and every prior choice preserved, and never writes.
  - **Scroll-to-top on every wizard step transition** (`hx-swap="innerHTML show:top"`) — an error
    re-render was landing above the user's scroll position on the long column-picker page,
    making it easy to miss entirely.
  - **A blank date or amount cell skips the row instead of failing the whole import**: some
    transaction types in a real export legitimately leave one of these blank for that row (an
    account-fee row with no "Data operacji"; a domestic transaction leaving a foreign-currency
    amount column empty) — `parse_rows` now treats a blank the same way it already treats a
    fully-blank row, while a non-blank-but-malformed value still raises.
  - **Columns blank in every row are hidden from the picker** — the real sample file has 101
    columns, only ~30 ever populated in any row; scans the whole file, not just the preview
    sample.
  - **Live per-column parse-check + blank-count warning** in the setup form (date/amount
    selects now trigger a reparse on change) and the same blank-count warning in the final
    preview — this is what makes a wrong column choice (a sparse/foreign-currency-only amount
    column, e.g.) visible *before* Save rather than as a mysteriously small transaction count
    after. Column selections also stopped resetting on every settings-only change, since losing
    every pick each time one setting was tweaked was worse than the rare stale-selection case.
  - **Optional `description_fallback` mapping column**: used only when the primary description
    column is blank for a row (a wire transfer's merchant-name column empty, payee/memo column
    populated) — confirmed against a real transfer row in the sample file.
  - **`/import/mappings` page**: lists every saved mapping (Edit/Delete) and the full import
    history log (`data/import_history.toml`, `app.models`/`storage.import_history`, written by
    `confirm`). Edit reuses the existing upload → setup → preview flow (a link to `/import` with
    the bank pre-filled and a `force_setup` flag) rather than a parallel one. History entries are
    independent snapshots (bank/account name and counts at confirm time), not live references —
    deleting a mapping or renaming/deleting an account afterward never breaks a past entry.
  - *Verified*: unit/integration tests for every fix above; a live root-cause diagnosis of a
    real "0 transactions imported" report that turned out to be a stale seed-data account number
    (`"1234"`) never updated to the real IBAN, not an importer bug; a real test-isolation gap
    this surfaced and fixed along the way — `IMPORT_HISTORY_PATH` wasn't in the integration test
    fixture's monkeypatched paths, so the new history tests had been writing fake entries into
    the real project's `data/import_history.toml` until caught and fixed.

- [x] **Phase 4 — Rule engine (bulk reclassification)**: `/rules` CRUD UI (add/edit/delete,
  addressed by list position — a rule has no natural unique key and CLAUDE.md's finalized
  `config/rules.toml` schema wasn't extended with one), save-time regex compile-checking
  (`services.categorizer.compile_pattern`, reused by the existing apply-time check), and a
  preview-then-apply bulk reclassification run: `plan_reclassification` computes the full
  before/after diff (old vs. new category/subcategory) for every row a rule run would change,
  skipping transfers (fixed `"Transfer"` category, never a rule target) and any row no rule
  matches (a non-match must never blank out an existing category — unlike import-time
  categorization's `DEFAULT_CATEGORY` fallback). The diff is threaded to the apply step as a
  hidden JSON field (the same no-server-session pattern as the import wizard's `rows_payload`),
  so `apply_reclassification` writes exactly what was previewed rather than recomputing against a
  ledger that may have moved on since — a change whose row was deleted in between is silently
  skipped rather than erroring. A newly-introduced category/subcategory is added to
  `config/categories.toml` on the fly via `ensure_category`, same as import-time categorization.
  One shared rule set, deliberately: every rule always applies both to future imports (already
  automatic since Phase 3) and to a manual reclassify run — no per-rule auto-apply toggle, to
  avoid a schema change beyond the finalized `config/rules.toml` shape. *Verified*: unit tests for
  `plan_reclassification`/`apply_reclassification` (matching row, already-correct row, no-match
  row, transfer, and an apply against a row deleted since preview) and `compile_pattern`
  (`tests/unit/test_categorizer.py`); integration tests for rule CRUD (including invalid-regex and
  blank-category rejection at save time) and the full preview→apply flow, including that transfers
  are never touched (`tests/integration/test_rules_router.py`); a live manual run confirming a
  "Gas station" rule correctly diffed 5 existing rows in preview, applied identically, and a
  second preview then showed no changes.

- [x] **Post-Phase-4 addendum — amount-bound rules**: optional `min_amount`/`max_amount` per rule
  (both inclusive, either/both may be unset), requested so one description pattern can split into
  different rules by transaction size — e.g. a gas-station chain that also sells
  groceries/car-washes, a big fill-up vs. a small in-store purchase. `services.categorizer
  .categorize` now takes the row's amount alongside its description and checks a rule's bounds
  against `abs(amount)` before its pattern is even tried; both import-time categorization and
  bulk reclassification pick this up automatically since they share one `categorize` call.
  `app.storage.rules` gained `_to_dict`/`_from_dict` (round-tripping `Rule(**entry)`/
  `rule.model_dump()` no longer suffice) to store the bounds as a quoted TOML string — same
  convention as `Account.starting_balance`, since `tomli_w` would otherwise serialize a bare
  `Decimal` as an imprecise float — and to tolerate a rules file written before these fields
  existed (a missing key means "no bound"). The rule form/table gained matching Min/Max amount
  fields and a save-time check that min ≤ max. *Verified*: unit tests for `categorize`'s amount
  matching (above/below each bound, inclusive edges, magnitude not sign, splitting one pattern
  into two rules by amount) and `app.storage.rules` round-tripping (including a
  before-these-fields-existed fixture file); integration tests for rule-form validation (invalid/
  negative/min>max amounts); a live consolidation of the user's real `config/rules.toml` (20 rules
  → 7, merging same-outcome patterns with regex alternation) confirmed against real ledger rows
  that the merged patterns categorize identically to the originals.

- [x] **Post-Phase-4 addendum #2 — rule type filter + preview amount column**: a magnitude alone
  can't tell an expense from an income of the same size (a 150 outflow and a 150 refund both have
  `abs(amount) == 150`), so `Rule` gained an optional `type` (`TransactionType.INCOME`/`.EXPENSE`/
  `None` for "either") — never `TRANSFER`, rejected at both the model-adjacent validation layer
  (`app.routers.rules._parse_rule_type`) and implicitly by never being offered in the Type select,
  since a rule pinned to it would be permanently unreachable (rules never run against transfers).
  `categorize` now takes the row's actual `TransactionType` (importer derives it from amount sign
  before calling in; reclassification already has `txn.type`) and requires it to equal a rule's
  own `type` when one is set, checked before the pattern/amount-bound checks. Stored in
  `config/rules.toml` as a quoted enum-value string, same "empty means unset" convention as the
  amount bounds. Separately, `ReclassificationChange` gained an `amount` field, threaded through
  the JSON preview payload the same way every other field already was, so the reclassify-preview
  diff table shows each row's amount — requested after the amount/type rule splits made "what
  would this actually match" harder to eyeball from category names alone. *Verified*: unit tests
  for `categorize`'s type matching (pinned-to-income/pinned-to-expense on both an income and an
  expense, unset matches either, splitting one pattern+magnitude by type) and
  `app.storage.rules`/`plan_reclassification` round-tripping; integration tests for rule-type
  validation (persists, blank matches either, transfer rejected, unrecognized value rejected) and
  the reclassify preview showing both the amount and the type-filtered result; a live check
  confirmed a `min_amount`-bound expense-only "Fuel" rule and an income-only "Fuel refund" rule
  sharing one pattern correctly picked "Fuel" (not "Fuel refund") for a matching expense row.

- [x] **Post-Phase-4 addendum #3 — top uncategorized descriptions**: the `/categories` page's
  bottom now surfaces a "Top uncategorized descriptions" table — every still-`"Uncategorized"`
  description ranked by how often it recurs, with its count and total, pointing at good
  candidates for a new `/rules` entry (requested after manually querying the ledger for exactly
  this a few times). `services.aggregation.top_uncategorized_descriptions` ranks by **count**, not
  total spend, deliberately — a single large one-off uncategorized transaction is a worse rule
  candidate than a small but frequent merchant, and ranking by total would let the former crowd
  out the latter. Blank descriptions are excluded (not one merchant, just "no description") and so
  are transfers (never carry a category outside the fixed `"Transfer"` tree). *Verified*: unit
  tests (ranks by count not total, excludes categorized/blank/transfer rows, respects a `limit`)
  and integration tests for the categories page (empty state, ranking order, categorized rows
  excluded); full suite 346 passing; the live ranking was cross-checked against the same query run
  ad hoc against the user's real ledger beforehand and matched.

- [x] **Post-Phase-4 addendum #4 — exact-amount rule matching**: the rule form gained an "Exact
  amount" field — a friendlier alternative to typing the same value into both Min and Max amount
  by hand for a rule that should only match one specific amount ("no `>=`/`<=`, just `=`", as
  requested). It's not a new `Rule` field: `app.routers.rules._validate_rule` collapses it to
  `min_amount = max_amount = exact_value`, reusing the existing inclusive-range amount matching
  (`services.categorizer._amount_matches`) rather than adding a third code path, and rejects
  combining it with a separately-set min/max as ambiguous rather than silently picking one. The
  edit form round-trips a `min_amount == max_amount` rule back into the Exact-amount field alone
  (not as a min/max pair the user never actually typed), and the rules table shows `= X` instead
  of an `X–X` range for one. *Verified*: integration tests (sets both bounds, rejects combining
  with min/max, rejects an invalid value, edit-form round-trip, table display); full suite 351
  passing; a live check confirmed a `9.99` exact-amount rule matched a `-9.99` transaction but not
  a `-10.00` one.

- [x] **Phase 5 — Reporting & visualization**: `/reports` (net worth over time + annual
  income/expense summary with a YoY delta) and `/reports/{year}` (monthly breakdown with a MoM
  delta that chains across year boundaries, plus income/expense category/subcategory totals with
  a per-category YoY delta). All built on `services/aggregation.py`'s shared group-by layer,
  extended with `yearly_totals_with_yoy`, `monthly_totals_with_mom`, `category_breakdown`, and
  `net_worth_by_month` rather than one-off per-view logic — reusing `group_by_month_and_type`
  underneath every one of them. **Charting deviates from this doc's original plan**: net worth is
  rendered as plain inline SVG computed server-side (`app/routers/reports.py::_svg_line_chart`)
  rather than a vendored JS charting library — discussed with the user, who agreed given this
  app's otherwise-zero-JS-dependency posture, on the condition the chart data
  (`net_worth_by_month`) stays decoupled from its SVG rendering so a future move to a JS library
  (e.g. Chart.js) only touches the rendering function and template, not the aggregation layer.
  Net worth math: summing every account's starting balance plus *all* transaction amounts to
  date (any type, not per-account) makes transfer legs cancel automatically, since a transfer's
  two equal-and-opposite legs always net to zero across the combined total — no special-casing
  needed the way `group_by_month_and_type`'s `net_total` needs for its per-type subtotals.
  MoM/YoY comparisons are at the category level only, not subcategory (a deliberate scope cut
  from the PRD's literal "category and subcategory" wording, to avoid a second nested comparison
  pass for comparatively little value). Follow-up polish in the same phase, landed in two rounds:
  a standalone yearly income/expense bar chart was tried first, then **merged into the net worth
  chart itself** on request — one combined chart (`app.routers.reports._svg_net_worth_chart`)
  with monthly income/expense bars (magnitudes rising from a shared zero baseline — mixing a
  signed expense total with an unsigned bar height would read wrong) sitting behind the net worth
  line, all on one shared axis, since a separate yearly-granularity chart didn't line up with the
  line's monthly x-axis. The axis itself always starts at 0 (`_tick_bounds`), not a data-driven
  min/max range, per explicit request — round numbers readable at a glance rather than values
  that happen to line up with the data; the tick *step* was originally fixed at a flat 5,000 too,
  but a later round replaced that with `_nice_step` (picks whichever of 1/2/5 × a power of ten
  lands closest to `span / 5`) once real data reaching six figures crowded the axis with dozens of
  gridlines — see the entry below. Bars and line share the
  same green/red/`--pico-primary` convention as `.amount-positive`/`.amount-negative` elsewhere.
  Separately, the year-detail category breakdown tables gained each category/subcategory's
  configured icon (`app.routers.reports._category_icons`, looked up from
  `config/categories.toml` since the pure ledger aggregation has no knowledge of category
  config), reusing the transactions list's compact inline `.cat-cell-icon`/`category_icon()`
  convention rather than the categories page's larger card-style glyph slot. A third round added
  a month-to-month comparison *by category* (the PRD's category-level MoM, not just the
  all-categories-combined monthly table already there): `services.aggregation
  .category_monthly_totals` returns `{category: {month_key: total}}` for one year/type, and
  `app.routers.reports._category_month_matrix` turns that into one row per category (same order
  as the annual breakdown table above it) with one column per month (abbreviated Jan/Feb/... via
  `calendar.month_abbr`) plus a Total column — a month with no activity for that category renders
  as an explicit "—" rather than a missing cell, so every row stays the same width. A fourth round
  delivered the "future Monthly Budgets page" CLAUDE.md had flagged as not-yet-built, but folded
  into the existing month-matrix table rather than a separate page: `services.aggregation
  .subcategory_monthly_totals` (the subcategory-level counterpart to `category_monthly_totals`)
  plus `app.routers.reports._ring_geometry`/`_category_config` compute, per category *and*
  subcategory, a small SVG budget-utilization ring for each budgeted month — that month's spend
  against the category/subcategory's own single configured budget (there's no per-month budget in
  `config/categories.toml`), capped at a full closed ring past 100% of budget with the exact
  percentage always shown as text since the ring alone can't distinguish 100% from 300%. No ring
  at all (not a 0% ring) means no budget is set for that category/subcategory. A fifth round added
  three color tiers instead of one flat color: green under 75% (`.utilization-under`), amber
  75-99% (`.utilization-warning`, the same amber as the categories page's own budget-exceeds
  warning), red at/over 100% (`.utilization-over`, also bolded there). Amount and ring sit on one
  line per cell after a request to keep them adjacent rather than stacked, which widens the table
  enough that its Category column needed pinning via `position: sticky`
  (`.month-matrix-table th/td:first-child`, also `white-space: nowrap` so a long category name
  can't wrap and break the sticky column's row height) so scrolling right through a year's months
  doesn't lose track of which row is which. *Verified*: 32 new aggregation unit tests (YoY/MoM
  delta computation including the December→January boundary case, category/subcategory grouping
  and sorting, transfer-leg cancellation in the net worth series, the category-month matrix's
  zero-fill behavior, subcategory-monthly-totals grouping) and 16 new router integration tests
  including the combined chart's axis/bar geometry, the category-icon lookup, the month-matrix
  table, and all three budget-utilization tiers for both categories and subcategories
  (`tests/unit/test_aggregation.py`, `tests/integration/test_reports_router.py`); full suite 302
  passing; a live manual check against a throwaway isolated ledger (never the real project data)
  confirmed the combined chart, the annual/year drill-down tables and icons, the month-to-month
  matrix, all three utilization-ring color tiers, the no-wrap sticky category column, and the
  scroll behavior all rendered correctly. A sixth round replaced the net worth chart's flat
  every-5,000 y-axis tick step with `app.routers.reports._nice_step` after real (larger) data
  showed the flat step crowding the axis with ~27 gridlines — the classic "nice numbers" axis
  algorithm instead scales the step to the data (whichever of 1/2/5 × a power of ten lands
  closest to `span / 5`), landing on a small, readable handful of gridlines regardless of whether
  the range is in the hundreds or the hundreds of thousands. `_svg_net_worth_chart` keeps a
  `tick_step` override parameter so a caller (namely tests) can still force an exact step rather
  than depend on whatever a given dataset happens to compute. *Verified*: two router integration
  tests (adaptive step at a small scale, uncrowded axis at a six-figure scale) replacing the old
  fixed-step test; full suite 303 passing; a live manual check against a throwaway dataset styled
  after the reported crowding (net worth/income/expense reaching ~130,000) confirmed exactly 4
  clean gridlines (0/50,000/100,000/150,000) instead of dozens. A seventh round added a
  transaction-count column to the annual breakdown table (`CategoryTotal`/`SubcategoryTotal`
  gained a `count` field alongside `total`, since a large total from many small transactions
  reads very differently from the same total via one big one) and fixed category names wrapping
  in that table's half-width `.report-columns` cell (`.breakdown-table th/td:first-child`'s
  `white-space: nowrap`, wrapped in `.table-scroll` so the whole table scrolls horizontally if it
  overflows instead). *Verified*: unit tests for `category_breakdown`'s counts (per-category and
  per-subcategory) and an integration test confirming the "# Txns" column renders; full suite 338
  passing; a live check confirmed both the counts (2 Groceries transactions summing correctly to
  count 2, split 1+1 across two subcategories) and that a long category name renders without
  wrapping inside `.table-scroll`.

- [x] **Phase 6 — Launcher & polish**: `scripts/launch.py` starts `uvicorn` as a subprocess bound
  to `app.config.SERVER_HOST`/`SERVER_PORT` (loopback-only), polls `/health` until it responds (or
  bails out early via `process.poll()` if uvicorn exits first — e.g. the port's already taken —
  rather than waiting out the full readiness timeout on a server that already crashed), opens the
  default browser via `webbrowser.open`, then blocks on `process.wait()`. `SIGINT`/`SIGTERM` (and a
  `finally` block covering any other exit) terminate the subprocess, with a `kill()` fallback if it
  doesn't exit within 5s — there's never an orphaned uvicorn left running in the background.
  Invalid-regex (`services.categorizer.compile_pattern`, Phase 4) and malformed-CSV
  (`app.routers.import_`'s `UnicodeDecodeError`/`LookupError`/`ValueError` handling, Phase 3 +
  its addendum) error paths were already covered by the time this phase started — auditing them
  was this phase's "error-handling pass" work for those two. **Lock-conflict was the one real gap
  found**: `app.storage.lock.LockError` (raised when a write can't acquire its file lock within the
  timeout) was never caught anywhere, so a rare two-tabs-writing-at-once collision would have
  surfaced as a raw 500. Fixed with a single app-wide `@app.exception_handler(LockError)` in
  `app/main.py` — one handler covers every write call site rather than threading a
  `try/except LockError` through each router individually. Since the failing request's original
  `hx-target` could be a dialog, a table fragment, or a single row, the handler doesn't attempt to
  render an error into whatever that target was (risking replacing real content with an error
  message); instead it reuses the existing `htmx_events.toast` helper to fire a "try again" toast
  and sets `HX-Reswap: none` so the DOM is otherwise left untouched. *Verified*: an integration test
  monkeypatches a router's `write_accounts` to raise `LockError` and confirms the response is a
  toast (not a 500) with `HX-Reswap: none`
  (`tests/integration/test_lock_error_handling.py`); full suite 508 passing; the launcher was run
  live end-to-end — readiness-polling logic verified in isolation against both a delayed-200 dummy
  server (correctly detects readiness) and a subprocess that exits immediately (correctly bails out
  fast rather than waiting the full timeout), and the real fast-fail path was exercised against the
  actual app with its normal port already occupied by another running instance, correctly printing
  the "uvicorn exited before the server became ready" message rather than hanging.

## Code style

All Python follows PEP 8 (enforced via `ruff check` / `ruff format`), with PEP 257-style
docstrings on modules, classes, and public functions, and type hints on all signatures. Skip
docstrings only for trivial one-liners where the signature is self-explanatory.
