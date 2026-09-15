# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 0–2 and 2.5–2.7 are complete: storage/locking (Phase 1), a working HTMX UI for accounts
(CRUD, starting balance shown, click-through to filtered transactions), manual transaction
entry/edit/delete (with on-the-fly category/subcategory creation), transfers (create, edit-both-
legs-together, and pair-delete), balance display, a dedicated `/categories` management page
(add/rename/delete category and subcategory, each with an optional icon from a vendored 44-icon
set — shown inline in the transactions list, colored by transaction type, uniformly sized via
shared CSS custom properties, with a per-icon tooltip hint), and a redesigned transactions list
(month/type grouping and account filter — both now sticky across navigation via cookies) — all
covered by passing tests, and manually smoke-tested live via `scripts/seed_sample_data.py`. Every
add/edit/delete dialog form shares the same compact 2-column layout and fires an app-wide success
toast on completion. Three out-of-sequence addenda have also landed since 2.7: transfer editing
plus a dialog-form redesign; further icon-set polish (uniform sizing, tooltips, redraws,
fuel/parking icons); and per-category/subcategory monthly budgets (independent thresholds, no
cross-validation, with a non-blocking warning pill when a category's subcategory budgets exceed
its own — the actual spend-vs-budget report is still a future Monthly Budgets page). Phase 3
(bank CSV import) is also complete: an index-based per-bank mapping (`config/import_mappings/
<bank>.toml` — column indices, not names, since real bank exports can have duplicate header
names; delimiter/encoding/date-format/decimal-separator all configurable) captured once via a
setup wizard and auto-reused on every later import from that bank, a preview step showing
new/duplicate/filtered-out counts before anything is written (duplicates detected by date+amount+
description; an optional account-number column filters a multi-account export down to the
destination account), and import-time auto-categorization via `config/rules.toml` (defaulting to
"Uncategorized" — full rule-management UI is still Phase 4). Validated end-to-end against a real,
messy 101-column Windows-1250-encoded bank export, not just synthetic test data. A post-Phase-3
addendum followed once the wizard was used against a real account: mapping-setup validation now
runs before the mapping is persisted (a bad setting used to both crash *and* silently save a
broken mapping); a blank date/amount cell skips just that row instead of failing the whole
import; columns blank in every row are hidden from the picker and the current date/amount
column gets a live parse-check plus blank-count warning as you pick it; an optional
`description_fallback` column covers rows where the primary description is blank; and
`/import/mappings` now lists every saved mapping (edit/delete) alongside a full import history
log (`data/import_history.toml`). Phase 4 (rule engine) is also complete: a `/rules` management
page (add/edit/delete, sharing the standard dialog-form pattern, addressed by list position since
a rule has no natural unique key) with save-time regex compile-checking (extended from Phase 3's
apply-time-only check, so a bad pattern never reaches `config/rules.toml`), plus "Reclassify
existing transactions" — a preview-then-apply bulk run of the current rules against the whole
ledger, showing a full old→new category/subcategory diff per affected row before anything is
written (`services.categorizer.plan_reclassification`/`apply_reclassification`). One shared rule
set: every rule always applies both to future imports (already automatic since Phase 3) and to
this manual reclassify run — no per-rule opt-out, a deliberate scope decision to avoid a schema
change beyond the finalized `config/rules.toml` shape. Transfers and non-matching rows are never
touched (a non-match must never blank out an existing category). Phase 5 (reporting &
visualization) is also complete: a `/reports` page with one combined net worth chart — a line
(cumulative net worth, monthly) with paired income/expense bars on the same shared axis, plus a
value-axis that always starts at 0 with a step chosen by `app.routers.reports._nice_step` (the
classic "nice numbers" axis algorithm: whichever of 1/2/5 × a power of ten lands closest to
`span / 5`) rather than a flat step — a fixed every-5,000 step was tried first and rejected once
real data reached six figures and crowded the axis with dozens of gridlines — rendered as inline
SVG (`app.routers.reports._svg_net_worth_chart`),
no vendored JS charting library (a deliberate deviation from the original plan doc, kept
swappable later since the chart data and its SVG rendering are separate functions), plus an
annual income/expense summary table with a year-over-year delta. `/reports/{year}` drill-down
adds monthly income/expense/net breakdown with a month-over-month delta that chains correctly
across a year boundary, plus income/expense category-and-subcategory totals with a year-over-year
delta per category — each category/subcategory row shows its configured icon (looked up from
`config/categories.toml` in the router, since the pure ledger-only aggregation layer has no
knowledge of category config; blank when unset), reusing the same compact inline
`.cat-cell-icon`/`category_icon()` convention as the transactions list, plus a category-**and-
subcategory**-by-month matrix table per type (`services.aggregation.category_monthly_totals`/
`subcategory_monthly_totals`) — each row across the year's months (abbreviated Jan/Feb/...
headers), a month with no activity showing an explicit "—", in the same order as the annual
breakdown table above it. The expense month-matrix additionally shows a small SVG budget-
utilization ring next to each budgeted category/subcategory's monthly amount — that month's
spend as a fraction of its own single configured budget (`app.routers.reports._ring_geometry`;
`config/categories.toml` has one budget per category, not a separate one per month), capped at a
full closed ring past 100% with the exact (possibly >100%) percentage always shown as text
alongside it. Three color tiers, not one: green under 75%, amber (the same amber as the
categories page's own budget-exceeds warning) 75-99%, red at/over 100% (also bolded there, since
the ring alone can't distinguish 100% from 300%). No ring at all means "no budget set" — distinct
from a ring at 0%, "budgeted but nothing spent yet". Amount and ring sit on one line per cell
(`.budget-cell-row`), which widens the table enough that its Category column is pinned via
`position: sticky` (`.month-matrix-table th/td:first-child`, also `white-space: nowrap` so a long
category name doesn't wrap and break the sticky column's height) while the month columns scroll
underneath — otherwise scrolling right to see later months loses track of which row is
which. All of it reads through `services/aggregation.py`, extended with
`yearly_totals_with_yoy`/`monthly_totals_with_mom`/`category_breakdown`/`category_monthly_totals`/
`net_worth_by_month` alongside the existing month/type grouping — one shared group-by layer, not
one-off per-view logic. Known simplification: subcategory-level YoY isn't computed, only
category-level (see the module docstring). See `docs/implementation-plan.md` for the full phased
plan, finalized schemas, and per-phase status checkboxes/implementation notes.

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
and opens the browser. Styling is Pico.css (classless build) + a vendored JetBrains Mono variable
font — see "UI conventions" below.

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
  main.py         # FastAPI app factory, startup consistency check, router mounting
  templating.py    # shared Jinja2Templates instance (avoids a circular import into main)
  config.py         # resolves data/config paths, binds 127.0.0.1 only
  models/             # pydantic domain models: Account, Transaction/TransactionType, CategoriesByType,
    ImportMapping, Rule, ImportHistoryEntry
  storage/              # file I/O + locking — the ONLY layer allowed to touch data/ or config/ files
    lock.py, ledger.py, accounts.py, categories.py, rules.py, import_mappings.py,
    import_history.py  # (reports storage lands in Phase 5, if needed)
  services/                # business logic — pure functions, no direct file I/O
    consistency.py, accounts.py, balances.py, transactions.py, categories.py, aggregation.py,
    importer.py, categorizer.py
  routers/                  # FastAPI routers, one per feature area — thin HTTP/HTMX glue only
    accounts.py, transactions.py, transfers.py, categories.py, import_.py, htmx_events.py (shared
    helper, not a router)  # (rules.py, reports.py land in Phase 4-5)
  templates/                 # Jinja2 pages + HTMX partials, one subdir per feature area
  static/                     # pico.min.css, style.css, htmx.min.js, fonts/ (all vendored)
data/ledger.csv                # created on first run if absent
config/                          # accounts.toml, categories.toml, rules.toml, import_mappings/<bank>.toml
tests/unit/                       # storage/ + services/, tmp_path-isolated, never touches real data/config
tests/integration/                 # router-level, FastAPI TestClient; conftest.py redirects storage to tmp_path
scripts/seed_sample_data.py          # dev-only: resets data/config to fake sample data
scripts/launch.py                      # starts uvicorn, waits for readiness, opens browser (Phase 6)
```

`storage/` owns all disk access and file locking. `services/` holds business rules and must stay
unit-testable without touching disk (inject paths/tmp_path in tests). `routers/` stays thin.

### Storage module pattern (established in `app/storage/{lock,ledger,accounts,categories}.py`)

Every `storage/*.py` read/write module follows the same shape (also used by `rules.py` and
`import_mappings.py`, added in Phase 3 — the latter is the one exception with a directory instead
of a single fixed path, since there's one mapping file per bank; see its own module docstring):

- `read_x(path: Path | None = None) -> ...`: returns an empty list/dict if `path` doesn't exist
  yet (files are created lazily on first write, never at startup). If `path` is omitted, resolves
  `app.config.X_PATH` **at call time** — e.g. `path = path if path is not None else
  config.LEDGER_PATH`, importing `config` as a module (`from app import config`), not the
  constant by name. This is what lets tests redirect every caller (including routers) at once by
  monkeypatching the three `app.config` path attributes, rather than needing each storage
  function's default re-bound individually — see `tests/integration/conftest.py`.
- `write_x(items, path: Path | None = None) -> None`: same default-resolution pattern, then
  wraps the write in `app.storage.lock.file_lock(path)`, writes to a `<path>.tmp` sibling, then
  `Path.replace()`s it into place — atomic, so a crash mid-write can never leave a
  truncated/partial file.
- Tests always pass an explicit `tmp_path`-based path instead of relying on the default.
- Money fields (e.g. `Account.starting_balance`) are pydantic `Decimal`, but serialize to TOML as
  a **quoted string**, not a bare float — `tomli_w` writes `Decimal` as a bare TOML float literal
  and `tomllib` reads it back as Python `float`, risking silent precision loss. The same care is
  needed for `Transaction.amount` in the ledger CSV, which is already string-typed there.

### Service module pattern (established in `app/services/{consistency,accounts,balances,transactions}.py`)

Every `services/*.py` function is **pure**: it takes in-memory data (lists of `Transaction`,
`Account`, a `CategoriesByType`) and returns new in-memory data, raising `ValueError` on invalid
input — no reads or writes of `data/`/`config/` files, ever. Routers do the I/O: read via
`storage`, call a service function to validate/build/transform, write the result back via
`storage`. This keeps business rules (unique account IDs, transfer pairing, on-the-fly category
creation, sign normalization, referential-integrity guards on delete) unit-testable without
`tmp_path`/disk at all — this split now also covers `services/importer.py` (CSV parsing/
filtering/dedup) and `services/categorizer.py` (rule matching), both pure, added in Phase 3
(`services/aggregation.py` already existed, see below) — reuse it again for Phase 4's rule engine
rather than letting a router grow business logic of its own.

### HTTP form testing pattern

Router integration tests use the `client` fixture in `tests/integration/conftest.py`, which
monkeypatches `app.config.{LEDGER,ACCOUNTS,CATEGORIES,RULES,IMPORT_HISTORY}_PATH` and
`IMPORT_MAPPINGS_DIR` to `tmp_path`-based locations before constructing the `TestClient`. A real
gap here (missing `IMPORT_HISTORY_PATH` for one session) let tests write fake entries into the
real project's `data/import_history.toml` before it was caught — add a new line to this fixture
for every new `config.*_PATH`/`*_DIR` a future `storage` module introduces, not just the storage
module itself.

## UI conventions

- **Styling**: `app/static/pico.min.css` (Pico.css v2.1.1, classless build — styles semantic HTML
  directly, no utility classes needed) loads before `app/static/style.css` (small app-specific
  overrides: the font, and `.amount-negative`/`.amount-positive` via Pico's own
  `--pico-del-color`/`--pico-ins-color` variables so they adapt to light/dark automatically).
  Prefer Pico's existing elements/variables (`<article>`, `<dialog>`, `--pico-spacing`,
  `--pico-muted-border-color`, etc.) over new custom CSS. Because
  Pico's classless build keys off direct children of `<body>`, every page's content sits inside
  `<header><nav>...</nav></header><main>...</main>`, per `app/templates/base.html` — don't add
  content as a bare `<body>` child outside those two.
- **Font**: `app/static/fonts/jetbrains-mono-variable.woff2` (JetBrains Mono, variable weight),
  applied globally by overriding Pico's `--pico-font-family` in `style.css`. Vendored the same
  way as htmx and Pico — see the "no CDN" invariant below.
- **Every form is a native `<dialog>` modal**, styled by Pico, not a JS modal library — see
  `app/templates/accounts/list.html` / `app/routers/accounts.py` for the reference
  implementation; the transactions page has three side by side ("Add income", "Add expense",
  "Record transfer" — the last handled by a different router, see below). There is no more
  inline-swap-below-the-button form pattern anywhere in the app; new forms should be a dialog
  too, not a reversion to that. The pattern:
  - A `<dialog id="X-dialog">` with an empty `<div id="X-dialog-content" hx-on::after-swap="...">`
    lives once on the page. Trigger elements (`Add account`, `Edit`, `Add income`, `Add expense`,
    `Record transfer`) just `hx-get` into `#X-dialog-content`; the `hx-on::after-swap` handler on
    that div opens the dialog via `showModal()`, guarded by `if (!d.open)` so it's a no-op on an
    error re-render (calling `showModal()` on an already-open `<dialog>` throws).
  - When more than one dialog can hold rendered content at the same time (as with "Add income"
    and "Add expense" — either can be open independently), any element `id` inside the shared
    form partial must be parametrized per dialog (e.g. `transactions/_form.html`'s
    `id="transaction-form-{{ values.type }}"` and its per-type `<datalist>` ids) — duplicate DOM
    ids across the two dialogs would otherwise make `list=` autocomplete bind to the wrong one.
  - Trigger buttons get a semantic color via a CSS class (`.btn-income` green, `.btn-expense`
    red, `.btn-transfer` amber — `app/static/style.css`), overriding both Pico's base button vars
    (`--pico-background-color`/`--pico-border-color`/`--pico-color`) *and* its hover-state vars
    (`--pico-primary-hover-background`/`-border`), since Pico's `:hover`/`:focus`/`:active` rule
    reads the hover vars, not the base ones — override only the base and the color reverts to
    Pico's default blue on hover.
  - The dialog closes itself via `hx-on:close-dialog="this.close()"` on the `<dialog>`, plus a
    click-outside-to-close handler (`hx-on:click="if (event.target === this) this.close()"`).
    Escape-to-close is free (native `<dialog>` behavior).
  - The form inside posts back to the same content div (`hx-target="#X-dialog-content"
    hx-swap="innerHTML"`). **On validation error**: the router re-renders the form fragment with
    an `error` message and the user's submitted values preserved (routers build a `values: dict`
    from the raw form fields specifically so a rejected submission doesn't lose the user's
    input) — no `HX-Trigger` header, so the dialog stays open. **On success**: the router sends
    back the *table* fragment marked `hx-swap-oob="true"` (see each `_table.html`'s `oob` param)
    so it refreshes wherever it actually lives on the page (which may be a different router's
    fragment — see `app/routers/transfers.py` importing `render_table` from
    `app.routers.transactions` to refresh the transactions table from the transfer dialog), and
    sets response header `HX-Trigger: close-dialog`, which fires a bubbling DOM event that the
    dialog's own listener catches. This generic `close-dialog` event name is reused by every
    dialog on the page, not per-dialog-named, so adding a new dialog elsewhere needs no new JS.
  - Reuse this exact pattern for Phase 4's bulk-reclassification preview dialog rather than
    inventing a new one — it's a good fit for "load content into a dialog, confirm, refresh a
    table elsewhere on success."
- **The transactions list's month/type grouping is a genuine structural choice, not a display
  filter** — both dimensions are independently toggleable (`by_month`, `by_type` query params on
  `GET /transactions`), and each combination actually reorders/reshapes the transactions shown
  (turning type grouping off, for instance, interleaves all types chronologically — it doesn't
  just hide the type headers on an otherwise type-clustered list). This is why it's implemented
  as a full server round-trip (`hx-get` re-rendering `#transactions-table-wrapper`) rather than a
  client-side CSS/JS toggle: a pure-CSS "hide the headers" approach was tried and rejected during
  design because it can't fix the underlying sort order.
  - `app/services/aggregation.py::grouped_transaction_view(transactions, by_month, by_type)` is
    the single entry point — it always computes the full month-and-type breakdown
    (`group_by_month_and_type`) and then collapses whichever dimension is off (`merge_months` for
    month, clearing `.groups` for type), rather than having 4 separate aggregation code paths.
    **The transfer subtotal is total volume moved, not a signed sum** — every transfer is a
    balanced pair (one negative leg, one positive leg of equal size), so a plain sum is always
    zero; it's `sum(abs(amount)) / 2` instead, and stays associative under the month-merge (so
    merging doesn't need to re-derive it from scratch). Don't "fix" this back to a signed sum.
  - `GET /transactions` serves both the full page and (when `request.headers["HX-Request"] ==
    "true"`) just the table fragment — the same URL/route, branching on that header, rather than
    a second route for what's conceptually the same resource at different render granularity.
    The toggle buttons compute their own next-state URL server-side (flip one dimension, keep the
    other) and use `hx-push-url="true"`, so reload/back-button correctly restore the last-picked
    grouping.
  - **Known simplification**: after creating a transaction/transfer, the out-of-band table
    refresh (`render_table()`) always resets to the default (both groupings on, no account
    filter) rather than threading the page's current toggle/filter state through the unrelated
    create/transfer POST — that state isn't available there without extra plumbing (hidden form
    fields or parsing the `Referer` header), which wasn't judged worth it for a one-click-to-
    restore inconvenience.
  - Expand-all/collapse-all stays a pure client-side `onclick` (no round-trip — it doesn't change
    what data is shown, just whether an already-rendered `<details>` is open) and is only shown
    in the toolbar when `by_month` is on, since there's nothing to expand/collapse otherwise.
- **Account filter** (`account_id` query param, alongside `by_month`/`by_type`) filters the
  ledger to one account *before* aggregating — so subtotals/net-totals reflect only that
  account's activity, not the whole ledger with irrelevant rows hidden. Empty string means "all
  accounts" (the default), not `None` — every control always sends all three params explicitly,
  so there's one consistent "unset" representation rather than sometimes omitting the param.
  - The account `<select>` fires via `hx-trigger="change"` and carries its own `account_id`
    value automatically (it has `name="account_id"`), plus a static, server-rendered `hx-vals`
    JSON blob carrying the *current* `by_month`/`by_type` so switching the account filter doesn't
    reset grouping — and conversely, the grouping-toggle buttons' own hrefs always interpolate
    the current `account_id` so switching grouping doesn't reset the filter. All three controls
    must keep round-tripping all three params like this; dropping one from any single control's
    URL/vals silently resets it for that action.
  - When filtered to one account, the row-level Account column disappears (`txn_row(txn,
    accounts, show_account)` in `_table.html` — `show_account` is `not account_id`) since every
    row would show the same, now-redundant, name.
- **`<summary>` needs `display: grid`, not `display: flex`, for a right-aligned trailing
  value** — Pico appends a chevron via `summary::after`, and a pseudo-element inside a flex
  container becomes a real flex item. With `justify-content: space-between` and 3 flex items
  (label, value, chevron), the value lands in the *middle* slot, not flush right, at a position
  that shifts with the label's text width — which is exactly why the per-month net totals in
  `.month-section summary` didn't line up with each other. Fixed via `grid-template-columns: 1fr
  auto auto` (label fills remaining space; value and chevron are fixed-width, flush right) plus a
  `min-width` on the value so its own column width doesn't wobble with digit count either. Reuse
  this grid approach for any future `<summary>` that ends in a value — the flex version looks
  right for a single row and then visibly disagrees with its neighbors once there's more than one.
- **Page layout, top to bottom**: `.page-header-row` (`position: sticky`) holds the `<h1>` and
  the 3 colored entry CTAs (`.cta-row`) — these are the only things that stay pinned while
  scrolling. Below it, `.view-options` (in `_table.html`, so it re-renders with every table
  refresh) holds the account filter and the grouping/expand controls — deliberately *not*
  sticky, and visually set apart with a muted background band, so "actions" and "view options"
  read as two different kinds of control rather than one undifferentiated row of buttons. This
  split is itself the fix for an earlier "too many buttons in the same place" complaint — don't
  collapse the two rows back into one without a similar visual/semantic separation.
- **Row-level Edit/Delete are hover-reveal glyphs floating outside the table entirely, never a
  trailing table column, and never in a dedicated reserved gutter either.** Icons come from
  `app/templates/_icons.html` (shared stroke-SVG macros — `edit_icon()` blue, `delete_icon()`
  red); markup lives inside the row's *first* `<td>`, not a separate actions cell. `.row-actions`
  is `opacity: 0` until `tr:hover`/`tr:focus-within`, and `position: absolute; left: -4.25rem`
  relative to the row (`tr { position: relative }`) — floating into the page's own ambient side
  margin. **A first version carved out a dedicated gutter via `margin-left` on the `<table>`
  itself; that was reverted** — it left a permanent empty band down the left of every table even
  when nothing was hovered, which looked wrong on its own regardless of alignment. The table now
  keeps its natural, unindented position flush with the rest of the page. Trade-off knowingly
  accepted: on a narrow browser window (little ambient margin to begin with) the icons can sit
  close to, or past, the viewport edge — judged acceptable for a desktop-only local tool. Don't
  reintroduce a `margin-left`-based reserved gutter to "fix" that; it re-creates the exact look
  that was rejected.
  - The out-of-flow positioning itself is a correctness fix, not a style preference: in
    `transactions/_table.html`, each type-group renders its own `<table>`, so a normal *trailing*
    actions cell was sized differently per sub-table (1 icon for a transfer row, 2 for
    income/expense), throwing the Amount column's x-position out of alignment between them.
    Pulling the icons out of column flow entirely fixes that as a side effect — column widths
    never depend on how many action icons a row has.
  - Row padding (`padding-top`/`padding-bottom: 0.6rem` on these tables' `td`s) exists so a
    vertically-centered 2rem icon button doesn't spill into the row above/below when revealed —
    tuned against an actual screenshot, not derived from a formula; re-check it if icon size
    changes. **Reuse this exact pattern for every future table with row-level actions** (e.g.
    Phase 2.5's categories list) rather than inventing a different affordance.

## Core architecture

### Data files

- `config/accounts.toml` — `[[accounts]]` tables: `id, name, number, description,
  starting_balance`. `id` is a stable short code that **never changes** even if name/description
  does — transactions reference `id`, never the account name.
- `config/categories.toml` — **two separate trees**, under top-level `[income]` and `[expense]`
  tables, each mapping category name → `{icon, budget, subcategories}`, where `subcategories`
  maps subcategory name → `{icon, budget}` (`app.models.category.CategoriesByType`/
  `CategoryEntry`/`SubcategoryEntry`, keyed by `TransactionType.value`). `icon` is a key into the
  vendored icon set (`""` = unset). `budget` is a quoted non-negative decimal string (`""` =
  unset) — income categories/subcategories can never have one (rejected in
  `services.categories`); a category's budget and its subcategories' budgets are independent
  thresholds, never validated against each other (see `services.categories
  .subcategories_exceed_category_budget` for the non-blocking display-only warning when a
  category's subcategory budgets add up to more than its own). Income and expense never share
  categories — "Salary" has no business appearing on the expense side. Transfers don't use this
  tree at all; they get a fixed `category="Transfer"` (see `services.transactions
  .new_transfer_pair`). Editable by hand, via settings UI, or on the fly during transaction
  entry/import (`services.transactions.ensure_category` adds to the correct bucket by type,
  starting with no icon/budget). No automatic dedup — the app does not tidy this up.
- `data/ledger.csv` — single flat file, one row per transaction, columns (finalized order):
  `id, date, account_id, category, subcategory, description, amount, type, transfer_id, notes`.
  `amount` is a signed decimal string; `type` ∈ `income|expense|transfer`. This is the one file
  the whole app revolves around.
- `config/import_mappings/<bank_slug>.toml` (one file per bank, filename slugified from the
  `bank` field) — `bank, delimiter, encoding, date_format, decimal_separator`, plus `columns`:
  ledger field name → **0-based column index**, not column name (real bank exports can have
  duplicate header names — see `app.models.import_mapping`'s docstring). Required keys: `date`,
  `description`, `amount`; optional: `account_number` (filters a multi-account export down to the
  destination account, matched against `Account.number`) and `description_fallback` (used only
  when the primary `description` column is blank for a row). Captured once via the `/import`
  setup wizard, reused automatically on every later import from that bank; viewable/editable/
  deletable via `/import/mappings`.
- `data/import_history.toml` — `[[imports]]` tables, one per confirmed import:
  `timestamp, bank, account_id, account_name, new_count, duplicate_count, filtered_count`.
  Append-only activity log, not configuration (hence `data/`, not `config/`) — each entry is an
  independent snapshot, not a live reference to the mapping or account, so deleting either
  afterward never breaks a past entry. Viewable via `/import/mappings`.
- `config/rules.toml` — `[[rules]]` tables: `pattern, field, category, subcategory, priority`.
  Shared between import-time auto-categorization (`services.categorizer`, Phase 3 — only
  `field="description"` is interpreted so far) and Phase 4's bulk reclassification of existing
  rows (not built yet — the rule *file format* exists, but there's no CRUD UI for it).

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
- **Imported CSV rows are always income or expense, never a transfer** — a bank export has no
  reliable signal for "this outflow and that inflow are the same transfer" beyond amount/date
  proximity, too fragile to guess automatically (`services.importer`'s module docstring). A
  transfer between the user's own accounts simply imports as two independent rows. Amount sign
  (negative/positive) maps directly to expense/income with no flipping — unlike manual entry,
  which takes an unsigned magnitude plus an explicit type.
- **Regex rules must fail loudly** on invalid patterns — compile-check at save time, never
  silently match zero rows at apply time. Implemented so far in `services.categorizer._compile`
  (raises on an invalid pattern when applying rules at import time); the save-time check belongs
  to Phase 4's rule-management UI, not built yet.
- **Bulk reclassification always previews first**: a rule run against the ledger must show a full
  before/after diff per affected row (old category/subcategory → new, plus any other changed
  fields), never just a count. Preview output must match apply output exactly.
- Reports pull from **one shared aggregation layer**, `services/aggregation.py` — it already has
  `group_by_month_and_type` (backing the transactions list); Phase 5 extends it with
  category/subcategory grouping rather than duplicating a second group-by module. Not per-view
  one-off aggregation logic — wire new report views through it.
- **No CDN scripts or stylesheets, ever** — the app must run with no network access. Vendored so
  far: `app/static/htmx.min.js` (htmx 2.0.10), `app/static/pico.min.css` (Pico.css 2.1.1,
  classless), `app/static/fonts/jetbrains-mono-variable.woff2` (JetBrains Mono). Phase 5's
  charting library follows the same pattern: fetch once during development, commit the file,
  reference it locally — never a `<script src="https://...">` or `@import url(...)`.

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
2.5. Categories management (dedicated add/rename/delete UI, with per-category/subcategory icons)
2.6. Edit/delete transactions (transfers: delete-both-legs; in-place editing of both legs
   together added post-2.6, see below)
2.7. Account view improvements (starting balance shown, click-through to filtered
   transactions, sticky month/type grouping via cookie)
   — 2.5/2.6/2.7 are inserted between 2 and 3, not renumbered into the sequence below, to avoid
   churning phase numbers referenced elsewhere
3. Bank CSV import (mapping setup + reuse, import-time auto-categorization)
4. Rule engine (bulk reclassification with preview/apply)
5. Reporting & visualization (shared aggregation layer, drill-downs, MoM/YoY, net worth charts)
6. Launcher script + error-handling pass
