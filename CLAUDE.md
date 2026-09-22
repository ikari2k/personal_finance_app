# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 0–2 and 2.5–2.7 are complete: storage/locking (Phase 1), a working HTMX UI for accounts
(CRUD, starting balance shown, click-through to filtered transactions), manual transaction
entry/edit/delete (with on-the-fly category/subcategory creation), transfers (create, edit-both-
legs-together, and pair-delete), balance display, a dedicated `/categories` management page
(add/rename/delete category and subcategory, each with an optional icon from a vendored 78-icon
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
touched (a non-match must never blank out an existing category). A post-Phase-4 addendum added
optional `min_amount`/`max_amount` bounds per rule (both inclusive, either or both may be unset) —
requested so one description pattern can split into different rules by transaction size (e.g. a
gas-station chain that also sells groceries/car washes: a big fill-up vs. a small in-store
purchase). `services.categorizer.categorize` now takes the row's amount alongside its description,
matching a rule's bounds against `abs(amount)` (magnitude, not signed) before its pattern is even
tried. Stored in `config/rules.toml` as a quoted string, same convention as
`Account.starting_balance`, since `tomli_w` would otherwise serialize a bare `Decimal` as an
imprecise TOML float — `app.storage.rules` round-trips this manually (`Rule(**entry)`/
`rule.model_dump()` no longer suffice) and tolerates a rules file written before these fields
existed (a missing key means "no bound", same as an explicit empty string). A magnitude alone
can't tell an expense from an income of the same size (a 150 outflow and a 150 refund both have
`abs(amount) == 150`), so a follow-up added an optional `type` field too (`TransactionType.INCOME`/
`.EXPENSE`/`None` for "either" — never `TRANSFER`, rejected at save time, since a rule pinned to
it would be permanently unreachable: rules never run against transfers). `categorize` now also
takes the row's `TransactionType` and requires it to equal a rule's own `type` when one is set.
The reclassify-preview diff table also gained an Amount column (`ReclassificationChange.amount`)
after the amount-based/type-based rule splits made "what would this actually match" harder to
eyeball from category names alone. The `/categories` page's bottom now surfaces a "Top
uncategorized descriptions" table (`services.aggregation.top_uncategorized_descriptions`) — every
still-`"Uncategorized"` description ranked by how often it recurs (not by total spend — one big
one-off transaction is a worse rule candidate than a small but frequent merchant), pointing at
good candidates for a new `/rules` entry. Blank descriptions and transfers are excluded, same
reasoning as elsewhere: a blank description isn't one merchant, and transfers never carry a
category outside the fixed `"Transfer"` tree. The rule form also gained an "Exact amount" field —
a friendlier alternative to typing the same value into both Min and Max by hand for a rule that
should only match one specific amount. It isn't its own `Rule` field; `app.routers.rules
._validate_rule` collapses it to `min_amount = max_amount = exact_value`, reusing the existing
inclusive-range matching rather than adding a third amount-matching code path, and rejects
combining it with a separate min/max as ambiguous. The edit form round-trips a `min_amount ==
max_amount` rule back into the Exact-amount field (not showing it as a min/max pair the user never
actually typed), and the rules table shows `= X` instead of an `X–X` range for one. Phase 5
(reporting &
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
category-level (see the module docstring). `CategoryTotal`/`SubcategoryTotal` also carry a
`count` (number of contributing transactions) alongside `total`, shown as a "# Txns" column on
the annual breakdown table — a large total from many small transactions reads very differently
from the same total via one big one. That table's Category column also gets `white-space:
nowrap` (`.breakdown-table th/td:first-child`) since it sits in a half-width `.report-columns`
cell narrow enough that a long name would otherwise wrap; wrapped in `.table-scroll` so the whole
table scrolls horizontally instead if it doesn't fit. Two more post-Phase-5 addenda followed. A
`/reports/{year}/{month}` drill-down (reached by clicking a month name in the year page's Monthly
breakdown table) adds a spending pie chart — the month's top 10 expense categories, everything past
that folded into one "Other" slice — plus Income-by-category/Expense-by-category tables for that
month, via a new `services.aggregation.category_totals_for_month` and an inline-SVG
`_svg_pie_chart` in `app.routers.reports` (same no-JS-library convention as the net worth chart).
All three report views (`/reports`, `/reports/{year}`, `/reports/{year}/{month}`) also gained an
`account_id` query-param filter (filtering the ledger before aggregating, same convention as the
transactions list's own account filter) via a plain GET form — not htmx, since picking an account
reshapes the whole page rather than one fragment — whose selection propagates through every link
between the three views. Separately, transfer detection: an optional
`counterparty_account`/`counterparty_account_fallback` import-mapping column pair (see "Core
architecture" below) captures a bank's own counterparty-account-number column at import time, and a
new `/import` "Detect transfers" preview-then-apply dialog cross-references that against the user's
registered accounts to find existing income/expense row pairs that are secretly a transfer between
two of the user's own accounts, merging a user-confirmed subset into proper linked transfer pairs
(`services.transactions.find_transfer_matches`/`merge_into_transfer`/`apply_transfer_matches`) — see
the "Imported CSV rows..." invariant below for why this is trusted enough to automate where a plain
amount/date guess still isn't. A follow-up addendum then surfaced transfer activity on the reports
pages themselves: the annual summary (`/reports`) and monthly breakdown (`/reports/{year}`) tables
each gained a "Transfers" column — `YearlyTotal`/`MonthlyTotal` (`services.aggregation`) now also
carry `transfer_volume`, the same "total volume moved" magnitude convention as
`TypeGroup.subtotal`'s transfer handling elsewhere (`sum(abs(amount)) / 2`), purely informational
and never folded into `income_total`/`expense_total`/`net_total` or their YoY/MoM deltas. A year
with transfers but no income/expense now gets a row too (previously dropped entirely), so its volume
has somewhere to show. A further addendum made the transactions list's Category cell itself
directly editable: every non-transfer row's category/subcategory became a live picker
(`transactions/_table.html`'s `txn_row`), posting to `POST /transactions/{id}/category` on a
change and re-rendering just that one row (`transactions/_row.html`) rather than the whole table
— so an in-progress bulk recategorization pass doesn't keep resetting every month `<details>`
section back to its default open/closed state. Deliberately never creates a category/subcategory
on the fly, unlike every other category entry point in the app (manual entry, CSV import, bulk
reclassify) — only pairs already in the tree can ever be picked, double-checked server-side via
`services.categories.category_pair_exists` (never trusting the submitted value), so this is the
one path with no "add new" escape hatch. A subcategory's own text carries its category as a
trailing `"(Category)"` hint — a suffix, not a prefix, so type-ahead still finds it by the
subcategory's own first couple of letters. A row whose stored category/subcategory has since been
deleted (see the no-automatic-dedup stance above) shows its actual stale value plus a
"(not in category list)" suffix rather than silently defaulting to something else. Originally a
native `<select>` grouped via `<optgroup>` (browser type-ahead only jumps to the first match on a
fresh keystroke run); a later addendum replaced it with a hand-rolled type-to-filter combobox
(`.cat-combo`/`.cat-combo-input`/`.cat-combo-list`/`.cat-combo-option` in `style.css`, behavior in
`app/static/app.js` — the app's first real custom-JS component, vendored the same as htmx/Pico,
no CDN) so typing narrows the option list live via substring match instead. Every listener is
delegated on `document` rather than bound per `.cat-combo`, since htmx re-renders rows/the whole
table on swaps that would otherwise silently orphan per-element listeners. It only ever commits a
value clicked or Enter-selected from a real `.cat-combo-option`; Escape or clicking away without
picking one reverts the input to its last committed value (`data-display`) — free-typed text is
never sent to the server, preserving the "no add-new escape hatch" invariant above. Most recently,
accounts gained a `Account.account_type` field (`app.models.account
.AccountType` — `checking`/`savings`/`credit_card`/`cash`/`investment`/`other`, a fixed enum, same
"controlled vocabulary" convention as `TransactionType`, not free text) shown as a "Type" column on
`/accounts` and a required `<select>` on the account add/edit form, labeled via `ACCOUNT_TYPE_LABELS`
(`credit_card` isn't presentable as-is, unlike `TransactionType`'s own values). Defaults to `OTHER`
both on the model (so an `accounts.toml` written before this field existed still reads back fine)
and on the two POST routes' own `Form(...)` default, so any caller that doesn't send it (an old
test helper, a script) still succeeds rather than 422ing on a newly-required field. Purely a
label/grouping field — never affects balance math or any other business rule. Transfer detection
then gained a second, opt-in-per-row mode alongside `find_transfer_matches`: `services.transactions
.find_orphan_transfer_candidates` finds existing rows whose counterparty is a registered account but
has *no* matching row anywhere in the ledger to pair with (typically: no import data at all for that
account — e.g. no CSV export available for a linked savings sub-account). `synthesize_and_merge_transfer`
creates the missing leg (same date, opposite-sign amount, shared description — the same convention
`new_transfer_pair` already uses for a manually-recorded transfer) and links both via the existing
`merge_into_transfer`. Surfaced in the same `/import` "Detect transfers" dialog as a second, clearly
separate "New transfers to create" section — checkboxes default **unchecked** here (unlike ordinary
matches, checked by default), since every row in this section is a genuinely inferred transaction,
never one independently observed in any bank export, which is a materially bigger trust step than
merging two already-real rows. Most recently, the transactions list's toolbar gained a fifth
control: a Type filter (income/expense/transfer), narrowing which rows are shown at all — a
different thing from the existing "Group by" select, which only changes how an *unfiltered* list is
split into sections. `app.routers.transactions._filter_ledger` gained a `txn_type` parameter
alongside its existing account/category/subcategory ones, `list_transactions` gained a sixth sticky
cookie-backed query param (`txn_type`, same "explicit empty string overrides the cookie, absent
param falls back to it" convention as the other five), and every other toolbar control's
`hx-vals`/URL was updated to keep round-tripping it (the "every control must preserve every other
control's current value" rule already documented above). Accounts then gained a second display-only
enum field alongside `account_type`: `Account.status` (`app.models.account.AccountStatus` —
`active`/`closed`, defaults to `active` for the same backward-compat reason `account_type` defaults
to `OTHER`), shown as a "Status" column on `/accounts` and a required `<select>` on the account
form, labeled via `ACCOUNT_STATUS_LABELS`. Purely informational, same as `account_type` — a closed
account keeps its full transaction history, is never filtered out of any list or picker, and can
still be referenced by a transaction; `services.accounts.remove_account` already refuses to *delete*
an account with transaction history regardless of status, so "closed" is what marks an account
that's done being used day-to-day without losing its past. A closed row is visually muted on
`/accounts` (`.account-row-closed` in `style.css`) — recedes, doesn't disappear. The transactions
list then gained a transaction count next to each subtotal it already showed — a month's `<summary>`
label, the flat "Net total" row (month grouping off), and each type-group header — all via a plain
`{{ ...|length }}` on the already-available `month.transactions`/`group.transactions` list in
`transactions/_table.html`, no Python/service change needed (`.txn-count` in `style.css`). The
vendored icon set grew to 48 with an `apple` glyph (fresh produce/fruit) — same three-place addition
every new icon needs: `VALID_ICONS`/`ICON_HINTS` in `app/models/category.py`, plus a `*_icon()`
macro and `_icon_registry` entry in `_category_icons.html`; the picker grid and every render site
pick it up automatically since none of them hardcode the icon list. The month drill-down then
gained Previous/Next navigation either side of its own `<h1>` (`app.routers.reports._adjacent_month`
— plain `year*12 + (month-1) ± 1` div/mod arithmetic, wrapping across a year boundary), always shown
regardless of whether the adjacent month actually has any data (the page already renders a graceful
"No data" state for an empty month) and preserving the current `account_id` filter, same convention
as every other link between the three report views. The icon set grew twice more: `baby`/`star`/
`ferris-wheel`/`backpack`/`blocks` for a real Kids category tree, then `person`/`scissors`/
`lipstick`/`donate`/`cocktail` for a real Personal category tree — the latter batch also reused
three already-existing icons (`book`, `cap`, `x`) rather than drawing near-duplicates, since the
icon set isn't scoped to one category and a glyph like "book" or "the generic X" fits more than one
tree. `/transactions` also gained a free-text description search (`search` query param/cookie,
same sticky-filter convention as `account_id`/`category`/`subcategory`/`txn_type` — explicit empty
overrides the cookie, an absent param falls back to it), applied as a case-insensitive substring
match against `description` in `_filter_ledger` before every other filter. It's the toolbar's one
non-`<select>` control, a text `<input type="search">` debounced via `hx-trigger="input changed
delay:400ms, search"` rather than firing on every keystroke; every other toolbar control's
`hx-vals` now round-trips `search` too, per the toolbar's standing "every control must carry every
other control's current value" rule. A follow-up fixed three vendored icons whose actual drawn
ink sat noticeably off-center within their 16×16 box — `gift`, `coffee`, `gamepad` — each by
shifting its `<svg>`'s `viewBox` origin (e.g. `viewBox="0 2 16 16"` for `gift`) rather than
touching path coordinates: the box a flex-centered wrapper aligns is the *viewBox window*, not
the ink inside it, so an icon whose shape sits in (say) the lower half of that window still reads
as "sitting low" even though its wrapper box is perfectly centered. Found by walking every icon's
`d`/`rect`/`circle` geometry and comparing its centroid to the 16×16 midpoint — a handful of
others were flagged too (`tv`, `wifi`, `disc`, `refresh`) but left alone: their apparent offset
was a smaller appendage (afoot/stand) pulling a raw bounding-box calculation off without actually
looking off, or an arc-heavy path where a quick geometric check overestimates the arc's true
extent — not worth risking a visual regression on an already-shipped, already-looked-at icon
without being able to see the render (no browser tooling on this machine — verification here was
math on the path data, not a screenshot). The transactions list's row padding was also tightened
(`#transactions-table-wrapper td`, 0.6rem → 0.4rem top/bottom, its own override distinct from the
other three row-actions tables which keep 0.6rem) — still comfortably above the 2rem
button-clearance floor the shared rule exists for. The whole hand-drawn icon set was then swapped
wholesale for Lucide (https://lucide.dev, ISC license) — `app/templates/_category_icons.html`'s
macros are now Lucide's own published SVG markup verbatim (24×24 viewBox, `stroke-width="2"`,
fetched once from the `lucide-static` npm package and committed, same "fetch once, vendor, no CDN
at runtime" treatment as htmx/Pico/the font — the app itself never fetches Lucide at runtime, only
the one-time authoring step did). Every *key* that already existed (the
vocabulary `categories.toml` actually stores, e.g. `"baby"`, `"cart"`) kept its exact name — only
which artwork that key renders as changed, via `_icon_registry` in `_category_icons.html` — so no
existing category/subcategory icon assignment in real data needed touching, and no migration
script was needed; verified by checking every icon key in the real `config/categories.toml`
against `VALID_ICONS` post-swap. The set also grew from 58 to 78: 20 new keys added for concepts
the existing tree didn't have an icon for yet (medical, public transit, furniture, utilities,
pets beyond dogs, hospitality, family/shared expenses, payment methods) — chosen by cross-
referencing gaps against the real category/subcategory names already in use, not picked
arbitrarily. `--category-icon-size`/`--subcategory-icon-size` size overrides are unchanged, but
the three `stroke-width: 1.3`/`1.5` CSS overrides (tuned for the old hand-drawn set's 16×16/1.4
proportions) were removed rather than reworked — Lucide's own native `stroke-width="2"` at a
24×24 viewBox is almost exactly the same *relative* thickness those overrides were already
approximating by hand, so letting each icon's own baked-in attribute apply is simpler than
re-deriving new override numbers for the new viewBox scale. The `/categories` page's "Top
uncategorized descriptions" table was bumped from 10 to 20 rows (`top_uncategorized_descriptions`'s
`limit` passed explicitly at the one call site in `app.routers.categories.list_categories`, the
function's own default left at 10 for any future caller that doesn't care) — the page heading now
says "Top 20" to match. The icon set then grew to 80 with `spade` (card/tabletop games) and
`truck` (delivery, shipping), added specifically to fill the last two real gaps below, and every
category/subcategory in the real tree that still had no icon (37 of them, across both income and
expense) got one assigned — reusing an existing key wherever one fit (e.g. `key` for both
Housing→Rent and Transportation→Car Rental, `wrench` for both House Maintenace and Car
Maintenance, `x` for every "Other"/"Uncategorized" bucket) rather than minting a new key per
subcategory. Caught one self-inflicted bug while doing this: `services.categories
.update_subcategory`'s `budget` parameter defaults to `None`, meaning "no budget" — calling it
with only `icon=` set (no `budget=`) silently wiped Housing→Rent's existing 1500 budget, since the
function has no "leave budget alone" mode distinct from "clear the budget." Caught immediately via
a before/after diff of every non-icon field across the whole tree (the same habit used elsewhere
for reconciling ledger fixes), and fixed by re-calling with the existing budget carried forward
explicitly — this function's calling convention is a footgun worth remembering: any future
icon-only or name-only update through it must still pass the entry's *current* budget, not omit
it. The accounts list then gained a closed-accounts toggle: `#accounts-table-wrapper` starts with
a `hide-closed` class (CSS-only, `display: none` on `.account-row-closed` rows), flipped by a
plain client-side `onclick` button shown only when at least one account is closed — same "pure
display toggle, no cookie, resets to its default on every render" precedent as the transactions
list's own expand/collapse-all button, since which accounts are closed doesn't change from one
page load to the next the way a filter selection would. Most recently, a new `/dashboard` page (now
also where `/` redirects, replacing `/accounts`) gives an at-a-glance summary: a net-worth hero
stat with a MoM delta and a 6-month sparkline (`app.routers.dashboard._svg_net_worth_sparkline` —
a small inline-SVG area+line chart, deliberately not `reports._svg_net_worth_chart`, which is
sized and detailed for its own full page), this-month income/expense/net with MoM deltas, an
active-accounts list (closed accounts excluded, just a count linking to `/accounts`), a budget
status widget (categories at/over 75% of their monthly budget, reusing `reports._ring_geometry`
and its exact ring markup rather than a second implementation), top expense categories this month,
a "needs attention" panel (uncategorized-transaction count, pending transfer-detection candidate
count), and a recent-transactions list — plus the same three quick-add dialogs
(`/transactions/new/{type}`, `/transfers/new`) reused verbatim from the transactions page, so
adding an entry doesn't require leaving the dashboard. Every number is assembled from the existing
`services.aggregation`/`services.balances`/`services.transactions` functions; this router adds no
new business logic beyond the sparkline's own SVG geometry. Known simplification: the quick-add
dialogs' success response targets `#transactions-table-wrapper` out-of-band, which doesn't exist on
this page, so it silently no-ops — the dialog still closes and the toast still fires (both driven by
the `HX-Trigger` header, independent of the oob swap), but the dashboard's own widgets don't
live-update until the next full page load. Caught one real bug while building this:
`MonthlyTotal.expense_total` is signed (negative, matching the transactions list's own type-group
subtotal convention) — a first pass computed `net_total` as `income_total - expense_total`, which
for a signed negative expense actually *adds* it back, producing a net figure larger than income
alone. Fixed by taking `net_total` straight from `MonthlyTotal.net_total` (already correct)
instead of re-deriving it, and showing `expense_total` as `abs()` only for the stat tile's own
display, never for arithmetic. Design pass done via the `artifact-design` skill — a mockup was
published as an Artifact first, reviewed, then implemented for real against the app's actual
Pico/JetBrains Mono/Lucide system rather than the mockup's own styling. A follow-up round of small
dashboard tweaks landed after live review: "This month"'s Reports link now points at
`/reports/{year}/{month}` for the current month specifically, not the generic `/reports` landing
page; the Accounts widget no longer mentions closed accounts at all (they're simply excluded from
the list, no "N closed accounts hidden" note — `closed_count` was dropped from the router entirely
once nothing referenced it); Top categories grew from 5 rows to 10 (`TOP_CATEGORIES_LIMIT`). The
transactions list then gained a real description/notes split, addressing a standing confusion:
`Transaction.description` and `.notes` were already two separate model fields, but `notes` had no
UI presence beyond the add/edit form — nothing on the list ever showed it, so typing into it felt
like it went nowhere. The Description cell (`transactions/_table.html`'s `txn_row`) now shows
`notes` when set, falling back to `description` otherwise, and is itself inline-editable
(`.desc-inline-input`, same borderless-until-hover treatment as `.cat-combo-input`) — but that
input only ever writes `notes` (`POST /transactions/{id}/notes`, `services.transactions
.update_transaction_notes`, mirroring the category route's narrow-update pattern), never
`description`. `description` itself is now read-only everywhere once a transaction exists: the
full edit dialog (`transactions/_form.html`) renders it as a `readonly` input instead of an
editable one when `transaction_id` is set (new-entry creation still gets a normal editable field —
there's no import source to protect yet), and `update_transaction_route` enforces this
server-side too, unconditionally carrying forward `existing.description` rather than trusting
anything submitted for it — belt-and-suspenders, not just a client-side restriction. This keeps
`description` permanently trustworthy as what `services.importer`'s duplicate detection and
`services.categorizer`'s rule matching actually key off of, while `notes` becomes the one place
personal annotations live, unconditionally overriding what the list displays.
`update_transaction_notes` is allowed on a transfer leg (unlike category, which stays fixed to
`"Transfer"`) since a personal note doesn't interact with the category tree at all. The
transactions list's category glyphs then picked up the dashboard's own visual treatment — a soft
tinted rounded-square tile around the icon (`.cat-cell-icon`, matching `.dash-cat-glyph`) instead
of a bare colored glyph, plus a muted date column — after which a latent alignment bug surfaced:
the icon tile (a fixed 1.7rem square) is taller than a plain line of text, and once it became the
tallest content in a row, `#transactions-table-wrapper td` had no explicit `vertical-align`, so it
fell back to the browser default and read as visibly misaligned against the row's other cells.
Fixed with an explicit `vertical-align: middle` (the same rule the dashboard's own
`.dash-txn-table td` already had) — a good reminder that adding a taller fixed-size element to one
column can silently break an unrelated column's alignment if the row's own vertical-align was
never pinned down. A follow-up pass on the dashboard itself, after live review, landed several
small fixes: the "This month" widget's Reports link now points at `/reports/{year}/{month}`
instead of the generic `/reports` landing page; Accounts no longer mentions closed accounts at
all (`closed_count` dropped from the router — they're simply excluded, no "N hidden" note); Top
categories grew from 5 rows to 10; Budget status gained an explanatory caption
(`dash-widget-caption`) and its own inclusion threshold moved to 60% — a
`BUDGET_ATTENTION_THRESHOLD_PCT` constant kept deliberately independent of `_ring_geometry`'s own
75%/100% color-tier boundaries, which still drive `/reports`' ring colors and weren't touched; the
net-worth sparkline gained axis value labels (max/min, `.spark-axis-label`) after review flagged
that a scale with no numbers can be glanced at but not actually read — reserving a left-side label
gutter (`_SPARK_LABEL_GUTTER`) rather than leaving the two end-of-scale values unlabeled; and
Recent transactions switched from a flat "last 6 rows" cap to grouping by calendar day, showing
the 5 most recent *distinct dates with any activity* (`RECENT_DAYS_LIMIT`, one `<tbody>` per day
with its own header row) rather than a row count that could cut off mid-day. Three redesign
directions for `/reports`' net-worth chart were then explored as an Artifact mockup (smoothed
line + quiet bars; net-worth and cash-flow split into two independently-scaled panels; both
indexed to 100% at the range start) before picking pieces to build for real: `_svg_net_worth_chart`
now gives every month a fixed `PER_MONTH_W` (56px) instead of squeezing the whole history into one
flat 720px-wide chart, so `width` grows with the data (`pad_left + PER_MONTH_W * count +
pad_right`) rather than bars getting thinner as more months accumulate. The template wraps it in
`.net-worth-chart-scroll`, a horizontally-scrolling div capped at `chart.visible_width`
(`VISIBLE_MONTHS` = 12 months' worth) — a long history scrolls left for older months instead of
rendering illegibly dense. The y-axis value labels live in their own `<g id="net-worth-yaxis-pin">`
painted last (so its `chart-axis-bg` rect occludes whatever's scrolled underneath) and get
re-translated on every `scroll` event by a small inline script so they stay pinned to the
viewport's left edge — a frozen axis — while gridlines/bars/the line scroll normally with the
data; the same script sets initial `scrollLeft` to the far right on load, so the page opens
already showing the most recent months, not the oldest. The chart also gained x-axis labels for
the first time (previously month/year only appeared in hover tooltips and the two endpoint
captions) — a month abbreviation under every column, with the year shown only where it actually
changes (`_svg_net_worth_chart`'s `x_labels`, one `chart-x-label`/`chart-x-label-year` pair per
row) rather than repeating it under all 12+ visible months. `_svg_net_worth_chart`'s `rows` shape
changed from `(label, net_worth, income, expense)` 4-tuples to `(key, label, net_worth, income,
expense)` 5-tuples — `key` (`"YYYY-MM"`) is what the new x-axis labels are actually built from,
since a pre-formatted `"May 2024"` string can't cheaply tell you whether the year just changed.
Live review flagged the chart as too small relative to how much page width was sitting empty next
to it (a desktop-only local tool, per this file's own stance, has no narrow-screen budget to
protect) — `PER_MONTH_W` grew from 56 to 84px, `VISIBLE_MONTHS` from 12 to 15, chart `height` from
260 to 380, and every axis/label font-size and mark size scaled up to match. The whole chart +
scroll-wrapper + pinned-axis markup (previously only on `/reports`) then got extracted into a
shared `reports/_net_worth_chart.html` partial (expects `chart` in scope, included from both
`reports/list.html` and the new `reports/year.html` usage) once `/reports/{year}` gained the same
chart scoped to just that year. `app.routers.reports._net_worth_chart` is the new shared builder
behind both pages — it always runs `net_worth_by_month` against the *full* transaction history
first (net worth is a running cumulative total; computing it from only one year's transactions
would drop every prior year's contribution and restart the line from zero) and only filters the
resulting points down to one year *afterward*, when `year` is passed. A single year is always
≤ 12 months, comfortably under `VISIBLE_MONTHS`, so the year page's chart never actually needs to
scroll — the same markup just naturally renders without a scrollbar there. A follow-up made that
explicit: `_svg_net_worth_chart` now also returns `needs_scroll` (`count > VISIBLE_MONTHS`), and
the template only applies the fixed per-month pixel width/scroll wrapper when it's true; otherwise
(a single year, or any short history) the SVG drops its fixed `width`/`height` attributes and
`.net-worth-chart-fill` lets it stretch to `width: 100%` instead — every mark/label scales
uniformly since they're all in the same viewBox coordinate system, so this reads as "fill the
available page width" rather than sitting at a smaller native size with empty space beside it.

The transactions list's toolbar then gained two more filters and a redesign pass after live
review flagged it as crowded once search + date-range landed alongside the four existing dropdown
filters. `_filter_ledger`/`render_table`/`list_transactions` gained `date_from`/`date_to` — two
independent inclusive ISO `YYYY-MM-DD` bounds, same sticky-cookie "explicit empty overrides the
cookie, absent falls back to it" convention as every other filter here, parsed defensively
(`_parse_date` returns `None` rather than raising on a malformed value, so a stale/tampered cookie
degrades to "no bound" instead of a 500). The `.view-options` toolbar split into two rows —
Account/Category/Type/Group-by stayed on the first, while the two free-form filters (search text,
date range) moved to a second `.view-options.view-options-secondary` band below it, rather than
seven controls all crowding one line. The date inputs are grouped into one bordered
`.date-range-group` pill (a shared "–" separator between them) so the pair reads as one "date
range" filter concept instead of two separate label+input pairs. Search itself switched from
`type="search"` to a plain `type="text"` with its own vendored icon (`search_icon()` in
`_icons.html`, the same 16×16/1.4-stroke hand-drawn family as `edit_icon()`/`delete_icon()`) — a
native search input's own browser-drawn decorations (a clear button, and on some browsers a
magnifier glyph) were colliding with the app's own icon, so dropping the native `type` sidesteps
cross-browser inconsistency entirely rather than fighting it with more CSS. The category-breakdown
tables on `/reports/{year}` and `/reports/{year}/{month}` (annual breakdown, month-to-month matrix)
then gained click-through to `/transactions` — every category and subcategory name is now a link
that filters the transactions list to exactly that category/subcategory (never just the category
when a subcategory is clicked — `_txn_link`, a private macro in `reports/_category_breakdown.html`,
builds the querystring) *and* the exact time span the row/cell belongs to: the whole year for the
annual breakdown table, one specific calendar month per cell in the month-to-month matrix (a new
`app.routers.reports._month_date_bounds` resolves a `"YYYY-MM"` key to its exact first/last day via
`calendar.monthrange`, since months vary from 28-31 days), or the one month the whole page is
already scoped to on `/reports/{year}/{month}`. `_category_month_matrix`'s row dicts gained a
`category` field (subcategory rows previously carried only their own name, with no way to recover
which category they belonged to) and `_month_cells`'s per-month cell dicts gained a `key` field, so
the template has everything it needs to build each link without re-deriving it. `breakdown_table`
and `month_matrix_table` (both in `reports/_category_breakdown.html`) also gained `date_from`/
`date_to`/`account_id` parameters threaded from each router — `account_id` round-trips whichever
account filter the report page itself is currently scoped to, same convention as every other
cross-page link in the app. Live review flagged those new links as reading like "a wall of raw
blue underlined text" across an already-dense table — a new `.table-link` class (inherits the
surrounding text's own color, underlines only on hover/focus) replaces the browser-default link
look on all three link sites (category/subcategory names, each month-matrix cell's amount), same
"understated until you actually reach for it" treatment as the row-click-through elsewhere in the
app (e.g. the accounts list). The transactions toolbar then gained a "Clear filters" button —
shown only when `any_filter_active` (a new context flag, `account_id or category or subcategory
or txn_type or search or date_from or date_to`), same "only show a control when it's relevant"
habit as the accounts page's closed-accounts toggle. Resets every filter field explicitly to `""`
in one `hx-vals`, the same "explicit empty overrides the sticky cookie" convention every other
control here already follows, rather than a new code path. Deliberately leaves `by_month`/
`by_type` out of that `hx-vals` — grouping is a display preference, not a filter, so clearing
filters doesn't also reset how the (now unfiltered) list is grouped.

Every non-transfer row on `/transactions` then gained a third row-action icon (amber, matching the
app's transfer color elsewhere) — "Convert to transfer" — for the case a manually-entered or
imported row turns out to actually be money moving between the user's own accounts, something
`find_transfer_matches`/`find_orphan_transfer_candidates` can only catch automatically when a bank
export happened to record the counterparty account number. `services.transactions
.convert_to_transfer` is the manual counterpart: given an existing row and a user-picked
counterpart account (a `<select>` in a new dialog, `transactions/_convert_form.html`, excluding
the row's own account), it first tries the exact same "existing opposite-sign, equal-magnitude row
within `max_day_gap` days" match `find_transfer_matches` uses (via `merge_into_transfer`, in case
the other leg already exists unnoticed), and only synthesizes a brand-new leg (via
`synthesize_and_merge_transfer`) when nothing matches — reusing both existing transfer-merge
primitives rather than a third implementation. The row's own id/date/amount/account never change,
only its type/category/subcategory and the new shared `transfer_id`. The row-actions box needed
the same one-rem-wider offset the mappings table's own three-icon rows already use
(`#transactions-table-wrapper .row-actions`), since only non-transfer rows get the third icon while
transfer rows keep two — harmless, since each row's box is positioned independently.
See `docs/implementation-plan.md` for the full phased plan, finalized schemas, and per-phase status
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
  - Expand/collapse-all is a single button, pure client-side `onclick` (no round-trip — it
    doesn't change what data is shown, just whether an already-rendered `<details>` is open); its
    handler checks whether *any* target `<details>` is currently closed to decide which way to
    toggle, rather than two separate expand/collapse buttons. On the transactions list it's only
    shown when `by_month` is on, since there's nothing to expand/collapse otherwise; the
    `/categories` page reuses the exact same pattern (`.cat-row` in place of `.month-section`) in
    its own sticky `.page-header-row`, always shown since categories are always collapsible there.
- **Account/category/subcategory filters** (`account_id`/`category`/`subcategory` query params,
  alongside `by_month`/`by_type`) filter the ledger *before* aggregating — so subtotals/net-totals
  reflect only the filtered rows, not the whole ledger with irrelevant rows hidden. Empty string
  means "no filter on this field" (the default), not `None` — every control always sends all five
  params explicitly, so there's one consistent "unset" representation rather than sometimes
  omitting a param. All three filters AND together (e.g. category + subcategory narrows to rows
  matching both, not either).
  - **Toolbar control count is a deliberate, revisited decision**: an earlier version had three
    side-by-side selects (Account/Category/Subcategory) plus two grouping-toggle buttons plus two
    expand/collapse buttons — seven controls, reported as too noisy. Now four: Account, one
    combined Category select (subcategories nested per category via `<optgroup>` — a category's
    subcategories are already implied by which one is picked, so a separate top-level Subcategory
    control was redundant, not a second axis of information), one "Group by" select replacing the
    two grouping-toggle buttons (its four options are exactly the four `by_month`/`by_type`
    combinations), and a single Expand/collapse-all button (its own onclick checks whether *any*
    `.month-section` is currently closed to decide which way to toggle, rather than two separate
    buttons for the two directions).
  - Since one `<select>` can only submit its own single value under its own `name=`, the combined
    Category select and the Group-by select both use htmx's dynamic `hx-vals='js:{...}'` form (a
    JS object expression evaluated against `event`, not static JSON) to also read a second value
    off the just-selected `<option>`'s `data-*` attribute — `dataset.subcategory` for the Category
    select, `dataset.byType` for the Group-by select. This is the one exception to the otherwise
    fully-declarative htmx pattern used everywhere else in the app; still no separate `<script>`,
    just an inline expression.
  - Every control's `hx-vals`/URL must keep round-tripping every *other* current filter/grouping
    value — dropping one from any single control silently resets it for that action. The one
    deliberate exception: picking a new **category** always resets `subcategory` to `""` (baked
    into the Category select's own options, not a separate reset step) rather than preserving an
    old subcategory selection that may not belong to the newly-picked category.
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
- `data/ledger.csv` — single flat file, one row per transaction, columns (finalized order, plus
  one post-Phase-5 addendum column appended at the end so older files still read back fine —
  see `storage.ledger._from_row`):
  `id, date, account_id, category, subcategory, description, amount, type, transfer_id, notes,
  counterparty_account`. `amount` is a signed decimal string; `type` ∈ `income|expense|transfer`.
  `counterparty_account` is a raw, normalized bank account number captured at CSV-import time when
  the bank's export names the other party (blank otherwise — manual entries never set it); see
  `services.transactions.find_transfer_matches` below. This is the one file the whole app revolves
  around.
- `config/import_mappings/<bank_slug>.toml` (one file per bank, filename slugified from the
  `bank` field) — `bank, delimiter, encoding, date_format, decimal_separator`, plus `columns`:
  ledger field name → **0-based column index**, not column name (real bank exports can have
  duplicate header names — see `app.models.import_mapping`'s docstring). Required keys: `date`,
  `description`, `amount`; optional: `account_number` (filters a multi-account export down to the
  destination account, matched against `Account.number`), `description_fallback` (used only
  when the primary `description` column is blank for a row), and `counterparty_account`/
  `counterparty_account_fallback` (same primary+fallback shape as `description_fallback` — a bank
  typically splits sender vs. recipient account into two mutually-exclusive columns depending on
  transaction direction; captured onto `Transaction.counterparty_account`, consumed later by
  `services.transactions.find_transfer_matches`, never at import time itself). Captured once via
  the `/import` setup wizard, reused automatically on every later import from that bank;
  viewable/editable/deletable via `/import/mappings`.
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
- **Imported CSV rows are always income or expense at import time, never a transfer** — amount/date
  proximity alone remains too fragile to guess a transfer pairing from automatically
  (`services.importer`'s module docstring). Amount sign (negative/positive) maps directly to
  expense/income with no flipping — unlike manual entry, which takes an unsigned magnitude plus an
  explicit type. A transfer between the user's own accounts imports as two independent rows, exactly
  as before — **but** a mapping can now optionally capture the bank's own counterparty-account-number
  column (`counterparty_account`/`counterparty_account_fallback`), carried onto
  `Transaction.counterparty_account` unchanged. A *separate*, explicitly-triggered, always-previewed
  step — `services.transactions.find_transfer_matches`/`merge_into_transfer`/
  `apply_transfer_matches`, behind `/import`'s "Detect transfers" dialog
  (`app.routers.import_.preview_transfer_matches`/`apply_transfer_matches_route`) — cross-references
  that captured number against every *registered* `Account.number` to find existing income/expense
  row pairs that are secretly a transfer, and merges a user-confirmed subset of them (checkbox
  per pair — a wrongly-merged transfer has no "split back apart" UI yet, so exclusion happens before
  anything is written) into a proper linked transfer pair. This is a real account-number reference
  the bank recorded, not an amount/date guess, which is why it's trusted enough to automate at all;
  it never runs at import time itself, only afterward, on demand.
- **Regex rules must fail loudly** on invalid patterns — compile-check at save time, never
  silently match zero rows at apply time. Implemented so far in `services.categorizer._compile`
  (raises on an invalid pattern when applying rules at import time); the save-time check belongs
  to Phase 4's rule-management UI, not built yet.
- **Bulk reclassification always previews first**: a rule run against the ledger must show a full
  before/after diff per affected row (old category/subcategory → new, plus any other changed
  fields), never just a count. Preview output must match apply output exactly.
- **Renaming a category/subcategory propagates into `config/rules.toml` but never into the
  ledger.** These are two different kinds of reference: a ledger row's `category`/`subcategory`
  are free-text copies made at entry time (not a foreign key — Phase 2.5's "no automatic
  dedup/cleanup" stance is specifically about this, §4.2), so a rename correctly leaves existing
  rows alone. A rule is different — it's an ongoing instruction ("when X, categorize as Y"), and a
  rule left pointing at a stale name would keep silently recreating the old category on the fly
  (`services.transactions.ensure_category`) every time it fires, a real behavioral bug rather than
  a cosmetic mismatch. `app.routers.categories`'s update routes call
  `services.categorizer.rename_category_in_rules`/`rename_subcategory_in_rules` (pure — only
  touch matching `Rule.category`/`Rule.subcategory` fields) after a successful rename, only when
  the name actually changed, and the success toast names how many rules were updated. Don't
  extend this to delete — deleting a category/subcategory intentionally still doesn't touch rules
  or the ledger, matching the existing no-cleanup stance.
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
