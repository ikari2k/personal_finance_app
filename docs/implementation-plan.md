# Implementation Plan — Personal Finance Tracker

This elaborates `finance-app-prd.md` into concrete technical decisions and a phased build order.
Phases are implemented one at a time, each its own reviewable unit of work with its own commit(s).
`CLAUDE.md` is updated after each phase lands to reflect what became concrete during that phase.

**Status**: Phases 0–3 complete, plus three out-of-sequence addenda inserted after 2.7 (transfer
editing/dialog redesign, icon-set polish, and per-category/subcategory monthly budgets — see
their entries below). Phase 4 (rule engine) not yet started.

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
