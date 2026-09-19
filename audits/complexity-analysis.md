# Code Complexity & Coupling/Cohesion Analysis — finance_app

Scope: `app/` (routers, services, storage, models). Tests skimmed for coverage context only.
All line numbers verified by reading the actual source on 2026-09-19. No complexity-linter
(`radon`, `lizard`, etc.) was available in this session — cyclomatic complexity below is computed
manually by counting decision points (`if`/`elif`/`for`/`while`/`except`/`and`/`or`/ternary/comprehension-`for`)
+ 1, with the count shown for every function flagged. Where a claim would require a tool not
available here, it is marked **Unable to verify**.

---

## 1. Cyclomatic complexity

### Functions with complexity > 10

| Function | Location | Complexity | Basis |
|---|---|---|---|
| `_render_mapping_setup` | `app/routers/import_.py:221-351` | **16** | 1(`if text is None`) + 1(`if len(rows)<2`) + 1(`for i,name in enumerate(header_row)`) + 1(`for r in data_rows`) + 1(`if i<len(r) and r[i].strip()` → the `and`) + 1(`if sample is not None`) + 1(`if not columns`) + 1(dict-comp `for c in columns`) + 1(`if not blank` in `_blank_warning`) + 1(`if selected_date_column in sample_by_index`) + 1(`except ValueError` date) + 1(`if selected_amount_column in sample_by_index`) + 1(`except ValueError` amount) = 15 + base 1 = **16** |
| `_svg_pie_chart` | `app/routers/reports.py:360-448` | **14** | 1(list-comp `for`+`if amount>0` = 2) + 1(`if not items`) + 1(`if rest`) + 1(generator `for _,amount in rest`) + 1(generator `for _,amount in top`) + 1(`if total<=0`) + 1(`for index,(name,amount) in enumerate(top)`) + 1(`and` in `is_other = rest and name=="Other"`) + 1(ternary `css_class`) + 1(`if len(top)==1` / else) + 1(ternary `large_arc`) + 1(ternary `label_x,label_y = ... if fraction<1 else ...`) = 13 + base 1 = **14** |
| `_svg_net_worth_chart` | `app/routers/reports.py:230-335` | **13** | 1(`if not rows`) + 1(ternary `step = ... if tick_step is not None else ...`) + 3(three list/generator comprehensions building `net_worth_values`/`magnitudes`/the `max(...)` generator) + 1(nested `x_at`'s `if count==1`) + 1(main `for index,(...) in enumerate(rows)`) + 4(four more comprehensions building `path_d`, `y_ticks`, `labels`, `net_worth_labels`) = 12 + base 1 = **13** |

**Note on these two `_svg_*` functions:** most of their "complexity" by the letter of the counting
rule is mechanical — one-line, non-nested list comprehensions projecting `rows` into parallel
arrays (e.g. `[label for label, _, _, _ in rows]`), not deep branching logic. Cognitively they read
as a sequence of independent, short transforms, not a tangle of interacting conditions. `_svg_pie_chart`
is the more genuinely complex of the two: it has a real `if/else` (lines 408-425) selecting between
two different SVG-path-construction code paths *inside* the main loop, plus three ternaries feeding
into the same iteration.

No other function in `app/` exceeds complexity 10. The next-highest are `group_by_month_and_type`
(`app/services/aggregation.py:55-109`, complexity 8: 3 nested `for` loops — transactions→months→
types — plus an `if/else` for the transfer-volume-vs-signed-sum branch) and `list_transactions`
(`app/routers/transactions.py:111-210`, complexity 10: 5 fallback-to-cookie ternaries + 3 filter
`if`s + 1 template-selection ternary), which sits right at the threshold.

### Nested if/else depth

Nothing in `app/` nests if/else 3+ levels deep in the classic sense (i.e., an `if` inside an `if`
inside an `if`). The deepest *structural* nesting is `group_by_month_and_type`'s triple `for`
(`for key in ... → for txn_type in TYPE_ORDER → if txn_type == TRANSFER: ... else: ...`,
`app/services/aggregation.py:70-89`) — 3 levels of loop/branch nesting, not if/else specifically.
`_svg_pie_chart`'s `if len(top) == 1: ... else: ...` (`app/routers/reports.py:408-425`) sits inside
one `for` loop — 2 levels.

### Dispatch patterns (switch-statement equivalents)

`TYPE_ORDER = [TransactionType.EXPENSE, TransactionType.INCOME, TransactionType.TRANSFER]`
(`app/services/aggregation.py:19`) is iterated to build per-type groups
(`app/services/aggregation.py:75-89`) rather than an if/elif chain — this is already the
dict/list-dispatch style the task asked to check for, and it's already used correctly here. No
if/elif chain standing in for a switch was found elsewhere; Python has no `match` statement in use
in this codebase (none needed — no place branches on a closed set of >3 cases via if/elif).

---

## 2. Cognitive complexity

- **Recursion:** none found anywhere in `app/`. Every "walk a tree" operation (the category/
  subcategory tree in `app/services/categories.py`, `app/models/category.py`) is two-level-fixed
  (category → subcategory) and handled with plain nested dict access/comprehensions, never
  recursion.
- **Mixed abstraction levels:** `app/routers/import_.py::_render_mapping_setup`
  (`app/routers/import_.py:221-351`) is the clearest instance — in one function body it (a) decodes
  raw bytes, (b) parses CSV rows, (c) discovers which columns are non-blank across the *whole*
  file, (d) live-validates the currently-selected date/amount columns against the current
  format strings, and (e) assembles a ~25-key template context dict. That's parsing, validation,
  and presentation-context-building all in one function. It is the single best candidate in the
  codebase for extraction (see §3 remediation).
- **`app/services/importer.py::parse_rows`** (`app/services/importer.py:88-150`, ~63 lines,
  complexity ~9: not over 10, but worth naming here) mixes low-level CSV cell access with
  business rules (skip blank/zero rows, apply the description/counterparty
  primary+fallback-column convention) in one loop body — reasonably cohesive (it's still all "parse
  one row"), not flagged as a problem, but it's the second-clearest mixed-abstraction candidate
  after `_render_mapping_setup`.
- **Nested closures:** `_svg_net_worth_chart` and `_svg_pie_chart` each define one or two small
  nested functions (`x_at`/`y_at`, `point`) that close over the outer function's locals
  (`plot_left`, `y_min`, `cx`, `radius`, etc.). This is idiomatic for coordinate-transform helpers
  and doesn't add real cognitive load — each closure is 1-3 lines with an obvious single purpose.
- **Nested loops:** the only genuinely nested loop (not just sequential loops) is
  `group_by_month_and_type`'s month→type double loop (`app/services/aggregation.py:70-89`,
  discussed above).

---

## 3. Lines-of-code metrics

Real counts via `wc -l` on every file under `app/`:

```
19  app/models/account.py
21  app/routers/htmx_events.py
23  app/config.py
24  app/services/balances.py
25  app/models/import_history.py
26  app/templating.py
48  app/models/transaction.py
49  app/models/import_mapping.py
57  app/models/rule.py
60  app/storage/accounts.py
71  app/services/accounts.py
73  app/storage/import_history.py
74  app/main.py
77  app/storage/lock.py
84  app/services/consistency.py
98  app/storage/rules.py
104 app/storage/import_mappings.py
105 app/storage/ledger.py
109 app/storage/categories.py
175 app/models/category.py
175 app/routers/transfers.py
180 app/routers/accounts.py
232 app/services/categorizer.py
246 app/services/categories.py
265 app/services/importer.py
435 app/routers/transactions.py
445 app/routers/rules.py
461 app/routers/categories.py
482 app/services/transactions.py
600 app/routers/reports.py
625 app/services/aggregation.py
926 app/routers/import_.py
```

### Files over 300 lines (7 of 31)

| File | Lines | Why |
|---|---|---|
| `app/routers/import_.py` | **926** | By far the largest file — 23 top-level functions across 4 sub-flows (create/edit/run wizards, preview+confirm, detect-transfers). See §5 cohesion. |
| `app/services/aggregation.py` | 625 | 21 public functions/classes, all genuinely "aggregate the ledger for a report," but many distinct shapes (month/type grouping, yearly/monthly totals, category breakdowns, net worth, uncategorized-description ranking). |
| `app/routers/reports.py` | 600 | Routing *plus* a full inline-SVG chart-geometry engine (net worth line+bars, pie chart, budget-utilization rings, "nice numbers" axis algorithm) — a deliberate, documented choice (see module docstring) to keep charting logic out of `services/` since it's presentational, not business logic. |
| `app/services/transactions.py` | 482 | Manual transaction CRUD + transfer-pair CRUD + (new) transfer-matching/merging — three related but distinct sub-concerns. |
| `app/routers/categories.py` | 461 | Category + subcategory CRUD, both with add/edit/delete — inherently repetitive surface area (see §1's copy-paste finding). |
| `app/routers/rules.py` | 445 | Rule CRUD + bulk-reclassify preview/apply. |
| `app/routers/transactions.py` | 435 | Manual transaction CRUD + the list view's cookie-backed filter/grouping state. |

### Functions over 50 lines

| Function | Location | Lines | Splitting candidate? |
|---|---|---|---|
| `_render_mapping_setup` | `app/routers/import_.py:221-351` | 131 | **Yes** — see §1/§5. Extract "discover non-blank columns from the file" and "live-validate the selected date/amount column" into their own helper functions; the function currently does file-decoding, column-discovery, live-validation, and template-context-assembly in one body. |
| `save_mapping_setup` | `app/routers/import_.py:402-488` | 87 | Mostly a mechanical "build a `columns` dict from 7 optional form fields, then validate" — see §5's `_set_counterparty_columns` note; the remaining bulk is the `except` branch re-passing ~15 named args to `_render_mapping_setup`, which is inherent to that function's own long parameter list rather than a separate problem. |
| `_render_preview` | `app/routers/import_.py:496-565` | 70 | Borderline; does parse→filter→dedup→categorize→build-context in sequence, each step a one-line call into `services.importer`/`services.categorizer` — it's a thin orchestration function, not deeply nested logic. Low priority to split. |
| `confirm` | `app/routers/import_.py:566-630` | 65 | Loop building `Transaction` objects + `ensure_category` calls, then two writes + a history-append. Reasonable as one transactional unit; not a strong split candidate. |
| `dry_run_mapping` | `app/routers/import_.py:790-848` | 59 | Same shape as `save_edited_mapping`/`save_mapping_setup` (build mapping from form, validate/preview) — see the copy-paste note in the architecture-analysis companion report. |
| `group_by_month_and_type` | `app/services/aggregation.py:55-109` | 55 | Borderline. Already reasonably factored (nested loop is the actual algorithm, not accidental sprawl); low priority. |

### Classes over 500 lines

**None.** Largest classes in the codebase are pydantic `BaseModel`s (`Rule` at 8 fields, `ImportMapping`,
`Transaction` at 11 fields) and small `@dataclass`es (`TransferMatch`, `CategoryTotal`, etc.) — all
under 60 lines including docstrings. This is a mostly-functional codebase (routers/services are
plain functions operating on data, not methods on stateful objects), so "god class" is structurally
not the failure mode to look for here — see §5 instead for "god module."

---

## 4. Coupling metrics

Computed from every `from app.*` import line across `app/routers/`, `app/services/`, `app/storage/`
(verified via `grep -rn "^from app\." app/routers app/services app/storage`). **Ca** (afferent) =
number of *other* `app/routers|services|storage` modules (plus `app/main.py` where relevant) that
import from this one. **Ce** (efferent) = number of `from app.*` import lines this module itself
has (including `app.models.*`/`app.templating`, which are leaf dependencies every layer is allowed
to reach). **I = Ce / (Ca + Ce)** — 0 = maximally stable (everything depends on it, it depends on
nothing), 1 = maximally unstable (nothing depends on it, it depends on everything).

| Module | Ca | Ce | I |
|---|---|---|---|
| `routers/accounts.py` | 0 | 7 | 1.00 |
| `routers/categories.py` | 0 | 10 | 1.00 |
| `routers/import_.py` | 0 | 13 | 1.00 |
| `routers/reports.py` | 0 | 7 | 1.00 |
| `routers/rules.py` | 0 | 11 | 1.00 |
| `routers/transactions.py` | 1 | 9 | 0.90 |
| `routers/transfers.py` | 0 | 6 | 1.00 |
| `routers/htmx_events.py` | 6 | 0 | 0.00 |
| `services/accounts.py` | 1 | 2 | 0.67 |
| `services/aggregation.py` | 3 | 2 | 0.40 |
| `services/balances.py` | 1 | 2 | 0.67 |
| `services/categories.py` | 1 | 2 | 0.67 |
| `services/categorizer.py` | 3 | 2 | 0.40 |
| `services/consistency.py` | 1 (`app/main.py`) | 2 | 0.67 |
| `services/importer.py` | 2 | 4 | 0.67 |
| `services/transactions.py` | 4 | 4 | 0.50 |
| `storage/accounts.py` | 7 | 2 | 0.22 |
| `storage/categories.py` | 5 | 2 | 0.29 |
| `storage/import_history.py` | 1 | 2 | 0.67 |
| `storage/import_mappings.py` | 1 | 2 | 0.67 |
| `storage/ledger.py` | 8 | 2 | 0.20 |
| `storage/rules.py` | 3 | 3 | 0.50 |
| `storage/lock.py` | 6 | 0 | 0.00 |

**This is a textbook-correct instability gradient**, and it's the single strongest quantitative
finding in this analysis: routers sit at I≈0.9-1.0 (outermost, depended-on-by-nothing, free to
depend on everything below), storage sits at I≈0.2-0.3 (innermost, heavily depended upon, itself
depends on almost nothing beyond its own model + `lock.py`), and services sit in between at
I≈0.4-0.67. Nothing here is inverted. Treat this as a positive finding to preserve, not a problem
to fix.

### Most tightly coupled modules

By combined Ca+Ce: `routers/import_.py` (13), `routers/rules.py` (11), `routers/categories.py`
(10), `routers/transactions.py` (10), `services/transactions.py` (8). All of this coupling is
**appropriate layered coupling** — a router depending on several services/storage modules it
orchestrates is exactly what a router is for.

**Checked specifically for inappropriate service↔service coupling:** `services/transactions.py`
imports `normalize_account_number` from `services/importer.py` (`app/services/transactions.py:18`).
Verified `services/importer.py` does **not** import anything back from `services/transactions.py`
(its only imports are `app.models.import_mapping`, `app.models.rule`, `app.models.transaction`,
`app.services.categorizer` — confirmed via `app/services/importer.py:28-31`) — **no cycle**. This
is one-directional and appropriate (`transactions.py`'s new transfer-matching code reuses an
account-number-normalization helper that conceptually belongs with the importer that introduced it).

**Router→router coupling:** `app/routers/transfers.py:10` imports `render_table` from
`app/routers/transactions.py`. This is real same-layer coupling, but it is deliberate and
documented — `render_table` is explicitly *not* underscore-prefixed specifically so it can be
reused (`app/routers/transactions.py:57-75`'s own docstring: "Public (no leading underscore)
because it's reused across routers"). Not a hidden coupling; still worth naming as the one place
the router layer isn't strictly siloed per-feature.

**No circular imports found anywhere** in `app/routers` ↔ `app/services` ↔ `app/storage`: `grep`
across every file in those three trees found zero `from app.routers.*` imports inside
`app/services/` or `app/storage/`, and zero `from app.services.*`/`from app.storage.*` imports
that loop back to a higher layer. Dependency flow is strictly `routers → services → storage/models`,
one direction, exactly as `CLAUDE.md` states it should be.

---

## 5. Cohesion analysis

| Module | Rating | Note |
|---|---|---|
| `app/routers/accounts.py` | Good | Account CRUD only, nothing else. |
| `app/routers/categories.py` | Good | Category+subcategory CRUD; the one reach-outside-its-own-concern is renaming a category cascading into `config/rules.toml` (`app/routers/categories.py:264-272, 427-437`) — a real, deliberate cross-concern (documented in `CLAUDE.md`), not an accident. |
| `app/routers/import_.py` | **Mixed (weakest module)** | Started as "the CSV import wizard" (create/edit/run flows, `app/routers/import_.py:1-848`) and has since grown a second, only loosely related responsibility: transfer-pair detection and merging (`preview_transfer_matches`/`apply_transfer_matches_route`, `app/routers/import_.py:849-926`). The two share almost no code (transfer-detection doesn't touch any of the wizard's column-picker/preview machinery) and operate on different questions ("how do I parse this bank's CSV" vs. "which existing ledger rows are secretly a transfer"). This is the clearest "god module accumulating a second concern" instance in the codebase. |
| `app/routers/reports.py` | Mixed, but deliberate | Routing + a full SVG chart-geometry engine in one file. The module's own docstring justifies this explicitly as a presentational/business-logic split, not an oversight — noted as a **documented tradeoff**, not scored as harshly as `import_.py`'s case. |
| `app/routers/rules.py` | Good | Rule CRUD + reclassify preview/apply — both are "the rule engine's UI," genuinely one concern. |
| `app/routers/transactions.py` | Good | Manual transaction CRUD + the shared list view. `render_table`/`list_transactions` duplication (see companion architecture report) is a code-quality issue, not a cohesion one — both functions are squarely "render the transaction list." |
| `app/routers/transfers.py` | Good | Transfer CRUD only. |
| `app/services/accounts.py` | Good | Three functions, all "validate + mutate the account list." |
| `app/services/aggregation.py` | Good, but large | Every function is genuinely "aggregate the ledger for a report or list view" (matches its own module docstring) — cohesive by *theme*, just large because there are many report shapes now (month/type grouping, yearly/monthly totals, category breakdowns at year and month granularity, net worth, uncategorized-description ranking). Not the same failure mode as `import_.py` (one theme, many variations, vs. two themes). |
| `app/services/balances.py` | Good | Two functions, one purpose. |
| `app/services/categories.py` | Good | Category-tree CRUD only; explicitly documents *why* it doesn't also guard against ledger usage (a decision, not an oversight). |
| `app/services/categorizer.py` | Good | Rule-matching + reclassification + rule-renaming-on-category-rename — all "apply/maintain the rule engine." |
| `app/services/consistency.py` | **Best/clearest module** | One function, one job, zero cross-cutting concerns, no dependency beyond the two models it checks. |
| `app/services/importer.py` | Good | CSV row parsing, account-number filtering/normalization, dedup, transaction-building — all "turn a bank CSV into candidate transactions." |
| `app/services/transactions.py` | Mixed | Manual transaction CRUD + transfer-pair CRUD + (new) transfer-*matching* (`find_transfer_matches`/`merge_into_transfer`/`apply_transfer_matches`, added this session). The matching algorithm is a materially different kind of logic (a greedy pairing search) from the CRUD-style validate-and-copy functions around it — a reasonable candidate to extract into its own module if the file keeps growing, though not yet a real problem at 482 lines. |
| `app/storage/*.py` (all 7) | Good | Every module follows the exact same read/write/lock shape (documented in `CLAUDE.md`'s "Storage module pattern"); each is scoped to exactly one file/directory. |

**Clearest module:** `app/services/consistency.py` (single function, single job, minimal
dependencies, 84 lines). **Weakest module:** `app/routers/import_.py` (two distinct
responsibilities — CSV-import wizard and transfer detection — bundled into one 926-line file).

---

## Findings table

| # | Finding | Category | Severity | Location | Remediation |
|---|---|---|---|---|---|
| 1 | `_render_mapping_setup` is the longest (131 lines) and most complex (cyclomatic 16) function in the codebase, with 17 parameters | Complexity + LOC | **7** | `app/routers/import_.py:221-351` | See snippet below |
| 2 | `app/routers/import_.py` (926 lines) bundles two distinct responsibilities: the CSV-import wizard and transfer detection/merging | Cohesion / god module | **6** | `app/routers/import_.py:849-926` vs. rest of file | Extract lines 849-926 into a new `app/routers/transfer_detection.py` router, included separately in `app/main.py` |
| 3 | Copy-paste: `create_category`/`update_category_route`/`create_subcategory`/`update_subcategory_route` each rebuild the same `values` dict + try/except + `_render_form(...)` call shape; the rename-into-rules block is duplicated verbatim | Copy-paste | **5** | `app/routers/categories.py:198-227, 230-278, 317-352, 390-443` (rename block: 264-272 vs. 427-437) | See snippet below |
| 4 | Copy-paste: `render_table` and `list_transactions` independently re-implement the same cookie-fallback ledger filter | Copy-paste | **5** | `app/routers/transactions.py:57-108` vs. `111-210` | See snippet below |
| 5 | Long parameter lists (7-17 params) across the import wizard's form-building functions | Missing abstraction | **5** | `app/routers/import_.py:221-239` (`_render_mapping_setup`), `:402-418` (`save_mapping_setup`), `:713-724` (`_mapping_from_edit_form`) | Bundle the mapping-setup form's fields into one small dataclass/pydantic model passed around instead of ~15 loose params |
| 6 | `_svg_pie_chart` has cyclomatic complexity 14, including an `if/else` selecting between two SVG-path constructions inside its main loop | Complexity | **4** | `app/routers/reports.py:360-448`, branch at `:408-425` | Extract the two path-construction branches into `_pie_slice_path_full_circle(...)`/`_pie_slice_path(...)` helper functions |
| 7 | `_svg_net_worth_chart` has cyclomatic complexity 13, mostly from 7 parallel one-line comprehensions projecting `rows` | Complexity | **3** | `app/routers/reports.py:230-335` | Low priority — see note in §1; the comprehensions are simple and independent, not deeply nested. If addressed, precompute `net_worth_values`/`magnitudes` once and reuse rather than the current shape (already close to that). |
| 8 | `services/transactions.py` (482 lines) mixes CRUD-style transaction/transfer helpers with a distinct greedy-matching algorithm (`find_transfer_matches`) | Cohesion | **3** | `app/services/transactions.py:319-411` (the matching functions) vs. rest of file | If the file keeps growing, extract `TransferMatch`/`find_transfer_matches`/`merge_into_transfer`/`apply_transfer_matches` into `app/services/transfer_matching.py` |
| 9 | `all_balances`/`account_balance` are O(accounts × transactions) instead of O(transactions) | Efficiency | **2** | `app/services/balances.py:10-24` | Not urgent — see remediation note; respects the project's stated ~7,000-9,000-row scale assumption (`CLAUDE.md`), which explicitly rejects adding indexing to "fix" full-ledger-in-memory patterns |
| 10 | Router→router import: `transfers.py` depends on `transactions.py`'s `render_table` | Coupling | **2** | `app/routers/transfers.py:10` | No action — deliberate, documented (`app/routers/transactions.py:73-75`'s own docstring explains the public-by-design reuse) |
| 11 | No god classes, no circular imports, clean routers→services→storage dependency direction, textbook instability gradient (routers I≈1.0, storage I≈0.2-0.3) | — | — (positive finding) | `app/routers`, `app/services`, `app/storage` (whole tree) | None needed — preserve this |

---

## Remediation snippets

### #1 — `_render_mapping_setup`'s length/complexity/parameter count

The column-discovery loop and the live-validation blocks are the two independently-extractable
pieces. Minimal extraction (keeps the same call site, same behavior):

```python
# Before (inside _render_mapping_setup, app/routers/import_.py:273-287):
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if len(rows) < 2:
            error = error or "Couldn't find any data rows with this delimiter."
        else:
            header_row, data_rows = rows[0], rows[1:]
            for i, name in enumerate(header_row):
                sample = next(
                    (r[i].strip() for r in data_rows if i < len(r) and r[i].strip()),
                    None,
                )
                if sample is not None:
                    columns.append({"index": i, "header": name, "sample": sample})
            if not columns:
                error = error or "No non-empty columns found — check the delimiter."

# After — extract to a module-level helper:
def _discover_columns(text: str, delimiter: str) -> tuple[list[dict], str | None]:
    """Return (non-blank columns, error) for the column picker."""
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        return [], "Couldn't find any data rows with this delimiter."
    header_row, data_rows = rows[0], rows[1:]
    columns = []
    for i, name in enumerate(header_row):
        sample = next((r[i].strip() for r in data_rows if i < len(r) and r[i].strip()), None)
        if sample is not None:
            columns.append({"index": i, "header": name, "sample": sample})
    if not columns:
        return [], "No non-empty columns found — check the delimiter."
    return columns, None
```

This alone drops `_render_mapping_setup`'s own complexity from 16 to roughly 10-11 and its length
from 131 to ~100 lines, with no behavior change.

### #3 — categories.py copy-paste (rename-into-rules block)

```python
# Duplicated verbatim at app/routers/categories.py:264-272 and :427-437 — extract:
def _rename_in_rules(
    old_name: str, new_name: str, rename_fn, *args
) -> int:
    """Apply rename_fn if old != new; return how many rules actually changed."""
    if old_name == new_name:
        return 0
    rules = read_rules()
    renamed_rules = rename_fn(rules, *args, new_name)
    if renamed_rules == rules:
        return 0
    write_rules(renamed_rules)
    return sum(1 for old, new in zip(rules, renamed_rules) if old != new)

# Call sites become:
rules_renamed = _rename_in_rules(category_name, name, rename_category_in_rules)
rules_renamed = _rename_in_rules(sub_name, name, rename_subcategory_in_rules, category_name, sub_name)
```
(The second call site's `rename_subcategory_in_rules(rules, category, old_subcategory, new_subcategory)`
signature needs its args in the right order — adjust `_rename_in_rules`'s `*args` placement
accordingly; shown here as the shape of the fix, not a drop-in-exact signature match.)

### #4 — transactions.py `render_table`/`list_transactions` duplication

```python
# Both independently do:
#   ledger = read_ledger()
#   if account_id: ledger = [t for t in ledger if t.account_id == account_id]
#   if category: ledger = [t for t in ledger if t.category == category]
#   if subcategory: ledger = [t for t in ledger if t.subcategory == subcategory]
# Extract once:
def _filtered_ledger(account_id: str, category: str, subcategory: str) -> list[Transaction]:
    ledger = read_ledger()
    if account_id:
        ledger = [t for t in ledger if t.account_id == account_id]
    if category:
        ledger = [t for t in ledger if t.category == category]
    if subcategory:
        ledger = [t for t in ledger if t.subcategory == subcategory]
    return ledger
```
Both `render_table` and `list_transactions` then call `_filtered_ledger(account_id, category, subcategory)`
instead of repeating the four lines.

### #9 — `all_balances` O(accounts × transactions) — only if this scale assumption ever changes

```python
# Current (app/services/balances.py:10-24): fine at ~9,000 rows / a handful of accounts.
# If it ever needs to change, single-pass grouping is O(n) instead of O(accounts * n):
def all_balances(accounts, transactions):
    totals = {a.id: a.starting_balance for a in accounts}
    for t in transactions:
        if t.account_id in totals:
            totals[t.account_id] += t.amount
    return totals
```
Not recommended to apply now — `CLAUDE.md` explicitly treats the full-ledger-in-memory pattern as
an accepted, deliberate simplification at this project's stated scale.
