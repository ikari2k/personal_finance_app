# CLAUDE.md

Guidance for Claude Code when working in this repository. User-facing docs live in `README.md`;
the phased plan and finalized schemas live in `docs/implementation-plan.md`.

## What this is

A local, single-user personal finance tracker. No login, no database, no bank APIs, no network
beyond `localhost`. All data is plain files: CSV ledger + TOML config. The app is the sole writer
of those files; hand-editing while it runs is unsupported.

Stack: Python 3.11+ / FastAPI, Jinja2 + HTMX (minimal JS), CSV/TOML storage, `uv`, Pico.css
(classless) + vendored JetBrains Mono + vendored Lucide icons. All planned phases (0–6) are done;
work now is incremental features/fixes.

**Work in small, separately-verifiable units.** Verify, then update this file and the plan doc
if the change alters an invariant or convention here — don't append changelog prose.

## Commands

```
uv sync                                    # install deps
uv run uvicorn app.main:app --reload       # dev server
uv run pytest                              # full test suite
uv run pytest tests/unit/test_x.py::test_y # single test
uv run ruff check . && uv run ruff format .
uv run python scripts/launch.py            # server + open browser
```

`uv` is only on PATH inside this repo (via `.python-version` → pyenv 3.13.7). Run from repo root.

**NEVER run `scripts/seed_sample_data.py` against the live app** — it resets `data/` and
`config/` to fake data and already destroyed real data once (no backup exists). Only run it with
paths redirected to a scratch location.

## Layout and layering

```
app/models/     pydantic domain models
app/storage/    file I/O + locking — the ONLY layer touching data/ or config/
app/services/   pure business logic, no file I/O (ValueError on invalid input)
app/routers/    thin HTTP/HTMX glue: read via storage, call services, write via storage
app/templates/  Jinja2 pages + HTMX partials, one subdir per feature
app/static/     vendored pico, style.css, htmx, app.js, fonts
tests/unit/     tmp_path-isolated; tests/integration/  TestClient, conftest redirects storage
```

- **Storage pattern**: `read_x(path=None)` returns empty if the file is missing and resolves the
  default via `from app import config` **at call time** (so tests can monkeypatch `config.*`);
  `write_x(items, path=None)` takes `file_lock`, writes `<path>.tmp`, then `Path.replace()`.
  Money is `Decimal` in models but serialized as a **quoted string** in TOML/CSV (never a float).
- **Services stay pure** so rules are unit-testable without disk. Routers must not grow business
  logic.
- **Router tests**: the `client` fixture in `tests/integration/conftest.py` redirects every
  `config.*_PATH`/`*_DIR` to `tmp_path`. **Add a line there for every new path a storage module
  introduces** — a missing one once let tests write into the real `data/import_history.toml`.

## Data files

- `config/accounts.toml` — `[[accounts]]`: `id, name, number, description, starting_balance,
  account_type, status`. `id` never changes; transactions reference it. `account_type`/`status`
  are fixed enums, display-only (never affect balances or rules); both default for old files.
- `config/categories.toml` — separate `[income]` and `[expense]` trees: category →
  `{icon, budget, subcategories}`, subcategory → `{icon, budget}`. `icon` is a key in the vendored
  set (`""` unset); `budget` is a quoted non-negative decimal (`""` unset), expense only. Category
  and subcategory budgets are independent (only a non-blocking warning when they exceed).
  Transfers use a fixed `category="Transfer"`, outside the tree. No automatic dedup/cleanup.
  Every category/subcategory also has `bucket` (50/30/20 report): expense `""`/`need`/`want`
  (subcategory `""` = inherit; resolve via `services.categories.effective_bucket`); income
  category `""`/`excluded` (left out of the income base), income subcategories have none. In
  `update_category`/`update_subcategory`, omitting `bucket` **keeps** it (opposite of `budget`).
- `config/payee_aliases.toml` — the payee book: `[[rules]]` (`payee` + a `[rules.fields]` table of
  label → exact value; a row matches when it has *every* field with that value, case- and
  spacing-insensitive via `services.payees.spelling_key`, never a pattern; more fields win, then
  the newer rule = later in the file) and `skipped` (group keys left unnamed) and `split` (group keys, `<field>::<cluster>`, divided into one group per spelling). Legacy `[[aliases]]`
  (`spelling, payee`) are still read, as rules on `Description`, and rewritten as rules on the next
  save. Written only by `/payees`. Rule fields are the labels of the import mapping's `payee_fields` plus `Description`. Import precedence: category-rule payee > payee
  rule > mapping payee column.
- `config/budget_rule.toml` — `needs, wants, savings` whole-percent targets (default 50/30/20,
  must sum to 100 via `services.budget_rule.validate_targets`).
- `data/ledger.csv` — columns: `id, date, account_id, category, subcategory, description, amount,
  type, transfer_id, notes, counterparty_account, payee, source, payee_fields`. `amount` is signed; `type` ∈
  income|expense|transfer. New columns are appended at the end so older files still read.
- `config/import_mappings/<bank>.toml` — `bank, delimiter, encoding, date_format,
  decimal_separator`, `columns` = ledger field → **0-based column index** (not name; real exports
  have duplicate headers). Required: `date, description, amount`. Optional: `account_number`,
  `description_fallback`, `description_extra_1`/`_2` (appended to the primary/fallback text joined with ` | `, skipping
  blanks and repeats; shown as `ParsedRow.display_description`, saved as the row's *note*, never
  folded into `description`, so duplicate detection and rules still see the bank's own text), `payee_fields` (up to 5 `[[payee_fields]]` tables of `label, column`; the mapping's payee section: the order is a
  priority, the first with a value is what a row is grouped on; values are stored on each row by
  label in the ledger's JSON `payee_fields` column), `payee`/`payee_fallback` (the
  bank's name for the other party, prefilled in the preview), `counterparty_account`,
  `counterparty_account_fallback`.
- `data/import_history.toml` — append-only `[[imports]]` log; independent snapshots, not live refs.
- `config/rules.toml` — `[[rules]]`: `pattern, field, category, subcategory, priority`, plus
  optional `min_amount`/`max_amount` (inclusive, vs `abs(amount)`, stored as quoted strings;
  `app.storage.rules` round-trips them manually) and `type` (income/expense/None; never transfer).
  "Exact amount" in the form collapses to `min == max` in `_validate_rule`. Optional `payee`
  names who matching rows were paid to; it is applied at **import time only** (bulk
  reclassification never touches payees), and beats the mapping's payee column.

## Invariants — do not break

- **Transfers are two linked rows** (opposite-sign, equal magnitude, shared `transfer_id`). Never
  model one as a single row; never edit/delete one leg without the other.
- **`source`** = the import mapping's name that first added a row (`cred`); blank for manual
  entries and app-made rows; set once at import (`confirm` uses the posted `bank`) and carried
  through edits via `model_copy`. Existing rows were set to `cred` in one pass on 2026-11-22.
- **`payee` is a third text field, kept apart.** `description` = the bank's text (read-only, keys
  duplicate detection and rule matching), `notes` = personal, `payee` = the clean name of who was
  paid / paid you (free text, blank for transfers — `merge_into_transfer` clears it,
  `update_transaction_payee` refuses transfer legs). Filled by a rule, the mapping's payee
  column, the import preview's box or by hand; the transactions list has an inline payee dropdown
  (`.payee-combo`: existing payees plus the row's bank text; free text is valid) and search matches it. Never use it in duplicate detection.
- **Payee view (`/payees`) rules.** Naming a group (`services.payees.plan_naming`, with the fields the
  user ticked) saves one rule per distinct value combination and names every payee-less row that
  one of the *new* rules wins; an existing payee is never overwritten, an older more-specific rule
  keeps its rows, and transfers and `description` are never touched. Every change (name, rename,
  merge, fill) is previewed in a dialog from the same computation the apply step runs. A rename
  or merge also re-points the rules so imports follow.
  Payee categorization uses the payee fields configured on the import mapping plus one built-in
  field, **Description** (`DESCRIPTION_FIELD`, always the last tab): the transaction's own
  `description`, so rows imported before payee fields were saved can be named. It is left out of
  a row whose description only repeats one of its mapped values (`field_values_of`). The
  counterparty account is never a payee field; the description keeps its other jobs (duplicate
  detection, category rules, the fill tool's matching). Import matches `Description` rules too.
  `without_payee_fields` counts rows with no *mapped* field ("no mapped payee field saved"; they
  can still be named by Description).
  **Suggestions are built per payee field**: "Name these" has a tab per field (labels from the
  mappings first, then stray ones: `payee_field_labels`; cookie `payee_field`, stored URL-encoded
  because labels are free text like "Tytuł"), and `unnamed_groups(rows, book, label)` clusters only
  the rows that have a value for that field (`group_key`: store numbers, legal suffixes and
  `com`/`www` dropped). A group key is `"<label>::<cluster>"` (`make_key`/`split_key`), and
  `skipped`/`split` entries carry it, so skipping or dividing under one field never touches
  another. The same row can be offered under several fields; naming it under any one takes it out of
  all of them (only payee-less rows are ever counted). Inside a field there are two tabs (cookie
  `payee_tab`, default *single*): groups of one spelling, and of several. Dividing a group adds its
  key to `split`; each value is then its own group, `<label>::exact:<spelling_key>`
  (`row_group_key`, `group_members` and every naming/preview path accept those keys); "Group with
  similar" undoes it. Spellings count ignoring case and spacing (`distinct_spellings`).
  Each card shows which other fields its rows also have (`UnnamedGroup.other_fields`, "also
  Web address 14 of 28"), and opens a workspace (`GET /payees/group`, offered only when
  `has_more_detail`: other fields exist, or more spellings than `CARD_SPELLINGS`) with every field's
  coverage and values and the distinct *combinations* of the ticked fields
  (`services.payees.combinations`). The group's own field is ticked by default; ticking another
  field (alone or with it) builds a rule that can reach rows outside the group, each combination
  nameable on its own (`plan_naming(..., only=combo)` = one rule on all of the combo's fields).
  The workspace stays open after naming a combination; a malformed `combo` is ignored.
  `/import/mappings/{bank}/enrich` fills `payee_fields` on *existing* rows from an older CSV
  (matched on account, date, amount, bank text; only missing labels, never overwrites; ambiguous
  rows skipped).
  The list's sort is sticky like the Transactions filters (`payee_sort`/`payee_dir` cookies; a URL
  value wins, default name A–Z) and every swap re-renders in the remembered order.
- **Lock every write** to ledger/config files via `app.storage.lock.file_lock` (PID lockfile,
  stale locks auto-cleared). `LockError` is handled app-wide in `app/main.py` (toast +
  `HX-Reswap: none`).
- **Config writes leave backups**: `accounts/categories/rules/payee_aliases` writers call
  `storage.backup.backup_before_write` inside the lock — `<file>.bak` (previous version) plus
  `config/backups/<file>.<date>` (state before the day's first write, kept 30 days). The ledger
  and import history are not backed up. Restore by copying a backup over the file while the app
  is stopped.
- **Referential integrity lives in app code**: `services.consistency.check_consistency` runs
  (warn-only) at startup; keep it in mind when changing transfer/account-reference logic.
- **Imported CSV rows are income/expense only** (sign maps directly; no flipping). Transfers are
  detected afterward, on demand, in the previewed `/import` "Detect transfers" dialog, using the
  bank-recorded counterparty account number vs registered `Account.number` — never amount/date
  guessing. "New transfers to create" (synthesized missing leg) defaults to **unchecked**.
- **`description` is read-only once a transaction exists** (enforced server-side in
  `update_transaction_route`); it keys duplicate detection and rule matching. Personal annotations
  go in `notes`, which the list displays in preference to `description`.
- **Regex rules fail loudly** — compile-checked at save time and apply time.
- **Bulk reclassification always previews** a full per-row old→new diff; preview must equal apply.
  Transfers and non-matching rows are never touched.
- **Renaming a category/subcategory propagates into `rules.toml` but never the ledger**; delete
  touches neither.
- **Category pickers on `/transactions` never create categories** (server re-checks via
  `category_pair_exists`); the free-typed combobox text is never submitted. The import preview's
  per-row category select (`apply_category_picks`) follows the same rule; rule-assigned
  categories not in the tree still get created by `ensure_category` on confirm.
- **50/30/20 report** (`/reports/budget-rule`, dashboard widget): income = income rows minus
  `excluded` categories; Needs/Wants = expense spend by `effective_bucket`, untagged shown as
  Unclassified; Savings = signed net of transfer legs on `account_type=savings` accounts (closed
  included, investment excluded, withdrawals reduce it). No income in a period → amounts only,
  no percentages; a period still in progress with no income yet is `pending` (set in
  `period_summary`) and shows "Waiting for income" with no figures at all. The per-bucket trend
  (`budget_rule/_trend.html`, from `_trend_chart`) backs both the reports page and the dashboard
  widget (`compact=True`). A *closed* period (ends before today) "met the rule" per
  `services.budget_rule.rule_met`: savings ≥ target and needs/wants each ≤ target even if all
  unclassified spend landed in that bucket. Met periods get the double-rule seal (`.br-rule`,
  `.br-badge`, `.is-met`, trend check); the month in progress never does.
- **One shared aggregation layer** (`services/aggregation.py`) backs the transactions list,
  dashboard, and all reports — extend it, don't add per-view group-by logic.
- **No CDN scripts, stylesheets, or fonts, ever** — the app runs offline. Vendor files and commit.
- **Scale**: ~7–9k ledger rows; loading the whole CSV per request is deliberate. No
  indexing/pagination/caching.

## Gotchas worth remembering

- **Never rebuild a category/subcategory entry field-by-field.** `ensure_category` runs on every
  transaction entry, import and bulk reclassification and rewrites `categories.toml`; it once
  rebuilt entries from `icon`/`budget` only and silently wiped every category-level `bucket`
  (the user's 50/30/20 classification). Copy the whole entry (`{**entry, ...}`) and add a
  regression test whenever an entry gains a field.
- `services.categories.update_subcategory`/`update_category`: omitting `budget` means "clear the
  budget" (it once silently wiped one during an icon-only edit). Always carry the current budget.
- `MonthlyTotal.expense_total` is **signed (negative)**. Use `net_total` directly; `abs()` only
  for display.
- Transfer subtotal is total volume moved, `sum(abs(amount)) / 2`, not a signed sum (which is
  always 0). `transfer_volume` is informational and never folded into income/expense/net.
- `{% include %}` inherits the includer's whole context — two partials on one page must not expect
  same-named variables (e.g. `chart` vs `subcategory_chart`).
- Report numeric routes use `{year:int}`/`{month:int}` converters so static paths like
  `/reports/category` aren't swallowed by Starlette routing.
- Net worth is cumulative: always compute over the full ledger, then filter to a year afterward.
- Adding a vendored icon touches three places: `VALID_ICONS`/`ICON_HINTS` in
  `app/models/category.py`, plus a macro and `_icon_registry` entry in
  `app/templates/_category_icons.html`. Icon keys in stored data must stay stable; only artwork
  changes. Icons are Lucide markup, vendored verbatim (24×24, stroke 2).
- The net worth chart (`reports/_net_worth_line.html`) is a daily line (`aggregation.net_worth_by_day`,
  always computed over the full ledger, then sliced) with range tabs that swap via HTMX from
  `/reports/net-worth?range=`; the year report shows the same line without tabs and the dashboard hero embeds it `compact`
  (no headline; tab links carry `compact=1`). Hover is `app.js`
  reading `data-points`. Monthly income/expense bars are a separate chart below it.
- Charts are inline SVG built in Python (no JS chart library); chart data and SVG rendering are
  separate functions. Chart colors follow the entity, never its rank.

## UI conventions

- **Type, controls, buttons**: Instrument Sans (vendored, `--font-sans`) for labels/prose,
  JetBrains Mono (`--font-mono`) only for headline figures and amount classes; tabular numerals
  everywhere. Every toolbar/header/dialog-footer control is `height: var(--control-h)`. Solid
  buttons are only `.btn-income/-expense/-transfer` and a form's one submit; `.secondary`,
  `.outline`, `.btn-outline` all render as an outline (classless Pico has no such classes).
  Tokens live at the top of `style.css`; add new sizes/colors there, not inline.
- **Figures and color**: display every amount with the `|money` filter (`1,234.50`; never in a
  form `value=`). Green/red mean income/expense *identity* (buttons, chips, charts, income
  amounts); expense amounts in lists are plain ink. Currency (`config.CURRENCY`) shows once, on
  the dashboard's net worth. Month-over-month deltas on the dashboard compare to last month
  *up to the same day*.
- **Nav and the scope bar**: the header is sticky (`--nav-h`; `.page-header-row` sticks beneath
  it) with the current page marked `aria-current="page"`. Dashboard, Transactions and Reports
  fill `{% block scope %}` in `base.html` (breadcrumbs go in `{% block crumbs %}`, above it) with `global_scope(accounts, account_id[, period])`
  (`_scope_bar.html`, import *with context*): an optional month stepper plus the account select.
  The account is one sticky cookie (`account_id`, written by Transactions and, via middleware,
  by Reports/Dashboard URLs carrying `account_id`; report routes resolve it with
  `routers.scope.resolve_account`) — absent param = remembered value, `account_id=` = all
  accounts. The bar's form carries every other query param as hidden fields. Don't hand-roll
  prev/next links or account selects in a page body; use `period_stepper`/`global_scope`.
  The Dashboard follows the account (its 50/30/20 widget and transfer nudge always use all
  accounts). The month is the `scope_month` cookie, recorded when a month report or
  `/reports/budget-rule?month=` is visited; Transactions turns it into a date range *once* per
  change (`scope_month_applied` marker), then its own date filters win. While the range is exactly one month the From/To inputs are
  disabled (the bar owns the range; "All dates" leaves month mode), and entering month mode resets
  grouping to month & type. `?scope_month=` on
  `/transactions` applies a month explicitly. The Transactions bar mirrors the list's real
  range (an exact calendar month = stepper, anything else = empty picker) and is refreshed
  out-of-band after HTMX filter changes.
- **Pico classless**: all page content sits inside `<header><nav>` / `<main>` per `base.html`; no
  bare `<body>` children. Prefer Pico elements/variables over new CSS. Font via
  `--pico-font-family` in `style.css`.
- **Every form is a native `<dialog>`** (reference: `accounts/list.html` + `routers/accounts.py`):
  - One `<dialog id="X-dialog">` with an empty `<div id="X-dialog-content">` whose
    `hx-on::after-swap` calls `showModal()` guarded by `if (!d.open)`. Triggers `hx-get` into it.
  - The form posts back to the content div. **Validation error**: re-render the fragment with
    `error` and the submitted `values`, no `HX-Trigger` (dialog stays open). **Success**: return
    the table fragment with `hx-swap-oob="true"` plus `HX-Trigger: close-dialog` (and a toast).
    `close-dialog` is the one generic event for all dialogs.
  - Element ids inside shared form partials must be parametrized when several dialogs can be open
    at once (e.g. `transaction-form-{{ values.type }}`).
  - Semantic button colors via `.btn-income`/`.btn-expense`/`.btn-transfer`; override both Pico
    base *and* hover vars.
- **Transactions toolbar**: every control's `hx-vals`/URL must round-trip every other control's
  current value (account, category, subcategory, txn_type, search, date_from, date_to, by_month,
  by_type). Filters are sticky via cookies — explicit empty string overrides the cookie, an absent
  param falls back to it. Filters apply *before* aggregation so subtotals reflect filtered rows.
  Picking a new category resets subcategory. "Clear filters" leaves grouping alone.
- **Month/type grouping is structural, not cosmetic** (a full server round-trip, via
  `aggregation.grouped_transaction_view`), not a CSS toggle. `GET /transactions` serves the page
  or just the fragment depending on `HX-Request`.
- **Actions vs view options**: the sticky `.page-header-row` holds `<h1>` + colored CTAs;
  `.view-options` (not sticky, muted band) holds filters/grouping. Don't merge them.
- **Row actions** (edit/delete/convert) are hover-reveal icons positioned absolutely outside the
  table (`.row-actions`, `left: -4.25rem`), markup in the row's first `<td>`, never a trailing
  column or reserved gutter. Keep row padding ≥ the 2rem-icon clearance; pin `vertical-align:
  middle` on rows containing tall fixed-size elements.
- **`<summary>` ending in a value needs `display: grid`** (`1fr auto auto`), not flex — Pico's
  chevron pseudo-element becomes a flex item and breaks right alignment.
- **Pure display preferences** (expand/collapse-all, closed-accounts toggle, all-time/this-year
  toggle) are client-side `onclick`, no cookie, no round trip.
- **Known simplifications**: create/transfer success always re-renders the transactions table
  with default grouping/filters; dashboard quick-add dialogs' oob table swap silently no-ops
  there (dialog + toast still work); subcategory-level YoY isn't computed.
- **Custom JS** (`app/static/app.js`, e.g. the `.cat-combo` and `.payee-combo` comboboxes) uses listeners delegated on
  `document`, since htmx swaps orphan per-element listeners.

## Code style

PEP 8 via ruff. PEP 257 docstrings (purpose, params, returns) on public modules/classes/functions
except trivial one-liners. Type hints on all signatures. Match the surrounding code's comment
density and idiom.
