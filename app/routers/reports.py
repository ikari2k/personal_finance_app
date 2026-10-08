"""Routes for the reporting & visualization views.

Read-only — no forms, no writes. Every view pulls from the shared
``services.aggregation`` group-by layer (CLAUDE.md's "one shared
aggregation layer" invariant) rather than one-off per-view logic.

The net worth chart combines a line (cumulative net worth) with paired
income/expense bars, one shared axis, all rendered as plain inline SVG
computed here — not via a vendored JS charting library, a deliberate
choice (see CLAUDE.md) to keep the app's zero-network-dependency,
minimal-JS posture intact rather than trusting/maintaining a
third-party JS file. ``_svg_net_worth_chart`` is presentational
geometry, not a business rule, so it lives here rather than in
``services`` — a future swap to a JS charting library would only touch
this function and the template, not ``services.aggregation``'s data.
"""

import math
import statistics
from calendar import month_abbr, month_name, monthrange
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.models.category import CategoriesByType
from app.models.transaction import TransactionType
from app.routers import breadcrumbs
from app.routers.budget_rule import month_pair, rolling_summaries
from app.services.aggregation import (
    CategoryMonthPoint,
    CategoryTotal,
    SubcategoryMonthPoint,
    category_breakdown,
    category_mom_deltas,
    category_monthly_series,
    category_monthly_totals,
    category_movers,
    category_recent_monthly_totals,
    category_subcategory_monthly_series,
    category_subcategory_shares,
    category_totals_all_time,
    category_totals_for_month,
    category_yearly_series,
    forecast_month_end,
    monthly_totals_with_mom,
    net_worth_by_month,
    subcategory_monthly_totals,
    yearly_totals_with_yoy,
)
from app.storage.accounts import read_accounts
from app.storage.budget_rule import read_targets
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports", tags=["reports"])

# The month drill-down's own "Biggest movers" list — how many ranked
# categories it shows.
MOVERS_LIMIT = 10


def _savings_rate(net_total: Decimal, income_total: Decimal) -> Decimal | None:
    """Return ``net_total`` as a % of ``income_total``, or ``None`` if unearned.

    ``None`` (rendered as "—") rather than a division-by-zero or a
    meaningless 0% for a period with no income at all — same "nothing to
    compare against yields None" convention as every other delta in this
    module (e.g. ``YearlyTotal.yoy_delta``).
    """
    return (net_total / income_total * 100) if income_total else None


def _month_date_bounds(key: str) -> tuple[str, str]:
    """Return a "YYYY-MM" key's ``(first day, last day)`` as ISO date strings.

    Backs the category-breakdown tables' click-through to
    ``/transactions`` filtered to this exact month — ``monthrange``
    handles the varying month lengths (28-31 days, leap Februaries)
    rather than hardcoding one.
    """
    year, month = (int(part) for part in key.split("-"))
    last_day = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _category_config(categories: CategoriesByType, txn_type: TransactionType) -> dict:
    """Return per-category ``{"icon", "budget", "subcategories": {sub_name: {...}}}``.

    ``category_breakdown``/``category_monthly_totals`` are pure ledger
    aggregations with no knowledge of ``config/categories.toml`` (icons
    and budgets are config, not ledger data), so this lookup happens
    here and gets passed to the template separately, rather than
    teaching ``services.aggregation`` about category config. ``budget``
    is ``None`` when unset (income categories always have ``None`` —
    they can never carry one, see ``services.categories``).
    """
    tree = categories.get(txn_type.value, {})

    def _budget(raw: str) -> Decimal | None:
        return Decimal(raw) if raw else None

    return {
        name: {
            "icon": entry["icon"],
            "budget": _budget(entry["budget"]),
            "subcategories": {
                sub_name: {
                    "icon": sub_entry["icon"],
                    "budget": _budget(sub_entry["budget"]),
                }
                for sub_name, sub_entry in entry["subcategories"].items()
            },
        }
        for name, entry in tree.items()
    }


_RING_RADIUS = 7
_RING_CIRCUMFERENCE = 2 * math.pi * _RING_RADIUS


def _ring_geometry(amount: Decimal, budget: Decimal | None) -> dict | None:
    """Return SVG ring geometry for one budget-utilization cell, or ``None``.

    A ring is only ever drawn for a category/subcategory that actually
    has a budget set — an unset budget has nothing to compare spend
    against, so the template skips the ring entirely for that cell
    rather than showing a meaningless 0%. The fill is capped at 100% of
    the ring's circumference (matching the reference design: a category
    at 211% of budget still draws one full, closed ring, not a
    double-wound one) — the exact percentage is still shown as text
    alongside it, so going over budget stays visible even though the
    ring itself maxes out. ``tier`` drives the ring/text color: "under"
    (<75%, green), "warning" (75-99%, amber — the same amber already
    used for the categories page's own budget-exceeds warning), "over"
    (>=100%, red).
    """
    if not budget:
        return None
    pct = float(amount / budget * 100)
    fraction = min(pct, 100) / 100
    dash = _RING_CIRCUMFERENCE * fraction
    if pct >= 100:
        tier = "over"
    elif pct >= 75:
        tier = "warning"
    else:
        tier = "under"
    return {
        "dash": dash,
        "gap": _RING_CIRCUMFERENCE - dash,
        "tier": tier,
        "pct_label": f"{pct:.0f}%",
        "pct": pct,
    }


def _month_cells(
    month_amounts: dict[str, Decimal], budget: Decimal | None, month_keys: list[str]
) -> list[dict]:
    """Build one row's per-month cells: an amount, ring geometry, and extreme marking.

    ``extreme`` is ``"max"``/``"min"`` on the row's single highest/lowest
    month (the first chronological occurrence if there's a tie), or
    ``None`` on every other cell. A month with no activity at all (a
    zero amount) is never eligible for ``"min"`` — that's an absence of
    data, not a genuinely low month, so it would otherwise dominate a
    category that's only active a few months a year. ``"max"`` is
    unaffected (a magnitude can only be zero if literally every month
    is, which already skips marking anything at all, same as a flat
    nonzero row — neither has a real extreme to call out).
    """
    cells = [
        {"key": key, "amount": month_amounts.get(key, Decimal("0")), "extreme": None}
        for key in month_keys
    ]
    amounts = [cell["amount"] for cell in cells]
    if amounts and max(amounts) != min(amounts):
        max_idx = amounts.index(max(amounts))
        cells[max_idx]["extreme"] = "max"
        nonzero_amounts = [a for a in amounts if a]
        if len(nonzero_amounts) >= 2:
            min_idx = amounts.index(min(nonzero_amounts))
            if min_idx != max_idx:
                cells[min_idx]["extreme"] = "min"
    for cell in cells:
        cell["ring"] = _ring_geometry(cell["amount"], budget)
    return cells


def _row_avg_and_median(cells: list[dict]) -> tuple[Decimal, Decimal]:
    """Return a month-matrix row's mean and median across its shown months.

    Both are computed over every shown month, zero-filled ones included
    — same "divide by every month, not just active ones" convention as
    the category detail page's own Avg/mo figure (``category_detail``'s
    ``avg_per_month``), so "typical month" reads consistently with the
    rest of the app rather than skewed high by skipping quiet months.
    """
    amounts = [cell["amount"] for cell in cells]
    average = sum(amounts, Decimal("0")) / len(amounts)
    median = statistics.median(amounts)
    return average, median


def _category_month_matrix(
    breakdown: list[CategoryTotal],
    monthly_totals: dict[str, dict[str, Decimal]],
    subcategory_monthly: dict[str, dict[str, dict[str, Decimal]]],
    config: dict,
    month_keys: list[str],
) -> list[dict]:
    """Build category (+ subcategory) rows for the month-to-month comparison table.

    Reuses ``breakdown``'s existing order (already sorted by annual
    total descending) rather than re-sorting, so this table and the
    annual breakdown table list categories the same way; each
    category's own subcategories follow it in ``subcategories`` order
    (already sorted by total descending too). A month with no activity
    gets an explicit zero rather than a missing column, so every row has
    the same number of cells. Each cell also carries budget-utilization
    ring geometry via ``_ring_geometry`` — that month's spend against
    the category's (or subcategory's) own single configured budget,
    since ``config/categories.toml`` has one budget per category, not a
    separate one per month. Each row also carries its own ``avg``/
    ``median`` across those same months (see ``_row_avg_and_median``).
    """
    rows = []
    for category in breakdown:
        cat_config = config.get(category.name, {})
        cat_monthly = _month_cells(
            monthly_totals.get(category.name, {}),
            cat_config.get("budget"),
            month_keys,
        )
        cat_avg, cat_median = _row_avg_and_median(cat_monthly)
        rows.append(
            {
                "name": category.name,
                "category": category.name,
                "icon": cat_config.get("icon", ""),
                "indent": False,
                "monthly": cat_monthly,
                "total": category.total,
                "avg": cat_avg,
                "median": cat_median,
            }
        )
        sub_monthly = subcategory_monthly.get(category.name, {})
        sub_configs = cat_config.get("subcategories", {})
        for sub in category.subcategories:
            sub_config = sub_configs.get(sub.name, {})
            sub_cells = _month_cells(
                sub_monthly.get(sub.name, {}),
                sub_config.get("budget"),
                month_keys,
            )
            sub_avg, sub_median = _row_avg_and_median(sub_cells)
            rows.append(
                {
                    "name": sub.name,
                    "category": category.name,
                    "icon": sub_config.get("icon", ""),
                    "indent": True,
                    "monthly": sub_cells,
                    "total": sub.total,
                    "avg": sub_avg,
                    "median": sub_median,
                }
            )
    return rows


def _month_breakdown_rows(
    breakdown: list[CategoryTotal],
    config: dict,
    deltas: dict[str, object],
    forecasts: dict[str, object] | None = None,
) -> list[dict]:
    """Build expense-by-category rows for the month drill-down's budget/MoM table.

    Precomputes each row's ring geometry (``_ring_geometry``) and MoM
    delta here rather than in the template — same "geometry is a router
    concern, not a Jinja concern" split as ``_category_month_matrix``.
    ``deltas`` is ``category_mom_deltas``'s result; a category/subcategory
    missing from it (no activity in either month, or type mismatch) gets
    ``delta=None`` rather than a KeyError. ``forecasts`` (category name →
    ``MonthForecast``) is only passed for the in-progress current month;
    each category's ``projected`` is ``None`` when absent from it.
    """
    forecasts = forecasts or {}
    rows = []
    for category in breakdown:
        cat_config = config.get(category.name, {})
        cat_mom = deltas.get(category.name)
        sub_configs = cat_config.get("subcategories", {})
        sub_rows = []
        for sub in category.subcategories:
            sub_config = sub_configs.get(sub.name, {})
            sub_rows.append(
                {
                    "name": sub.name,
                    "icon": sub_config.get("icon", ""),
                    "total": sub.total,
                    "count": sub.count,
                    "ring": _ring_geometry(sub.total, sub_config.get("budget")),
                    "delta": (
                        cat_mom.subcategory_deltas.get(sub.name) if cat_mom else None
                    ),
                }
            )
        rows.append(
            {
                "name": category.name,
                "icon": cat_config.get("icon", ""),
                "total": category.total,
                "count": category.count,
                "ring": _ring_geometry(category.total, cat_config.get("budget")),
                "delta": cat_mom.delta if cat_mom else None,
                "projected": (
                    forecasts[category.name].projected
                    if category.name in forecasts
                    else None
                ),
                "subcategories": sub_rows,
            }
        )
    return rows


def _nice_step(span: float, target_ticks: int = 5) -> float:
    """Return a "nice" (1/2/5 × a power of ten) tick step for an axis spanning ``span``.

    A flat step (e.g. always every 5,000) either crowds the axis with
    dozens of labels once the data reaches into six figures, or wastes
    space on too few ticks for a small range — the classic "nice
    numbers" axis algorithm instead scales the step to the data,
    landing on roughly ``target_ticks`` gridlines regardless of
    magnitude. Picks whichever of 1/2/5 (times a power of ten) is the
    smallest that still keeps the step at or above ``span /
    target_ticks``, so the axis never ends up with *more* than roughly
    ``target_ticks`` steps either.
    """
    if span <= 0:
        return 1.0
    raw_step = span / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        nice = 1
    elif residual <= 2:
        nice = 2
    elif residual <= 5:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def _tick_bounds(values: list[float], step: float) -> tuple[float, float]:
    """Round ``values``' range outward to a multiple of ``step``, always spanning 0.

    Anchoring every tick to a multiple of ``step`` starting from 0
    (rather than the data's own min/max) keeps every label a round
    number you can read off at a glance, not one that happens to line
    up with the data — ``step`` itself is chosen by ``_nice_step``.
    """
    lo = min([*values, 0.0])
    hi = max([*values, 0.0])
    y_min = math.floor(lo / step) * step
    y_max = math.ceil(hi / step) * step
    if y_min == y_max:
        y_max += step
    return y_min, y_max


_MONTH_ABBR = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# Chart geometry constants shared with the template's scroll wrapper —
# the visible viewport is exactly VISIBLE_MONTHS wide, older months
# reachable by scrolling left, so PER_MONTH_W must match on both ends
# (see reports/list.html's inline max-width style). Sized to fill a
# typical desktop viewport rather than sitting small in one corner of
# the page — this is a desktop-only local tool (see CLAUDE.md), not a
# mobile layout, so there's no narrow-screen budget being traded away.
PER_MONTH_W = 84
VISIBLE_MONTHS = 15


def _svg_net_worth_chart(
    rows: list[tuple[str, str, Decimal, Decimal, Decimal]],
    *,
    account_id: str = "",
    per_month_width: int = PER_MONTH_W,
    height: int = 380,
    pad_left: int = 84,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 42,
    tick_step: float | None = None,
) -> dict:
    """Return template-ready SVG geometry: a net worth line plus income/expense bars.

    ``rows`` is ``(key, label, net_worth, income_total, expense_total)``
    quintuples, oldest first, all for the same set of months — ``key``
    is the "YYYY-MM" form (see ``services.aggregation.NetWorthPoint``),
    used both to build the x-axis month/year labels below and each bar's
    own click-through link into ``/transactions`` (``income_link``/
    ``expense_link``, scoped to that exact month and type — ``account_id``
    propagates whichever account filter the page itself is currently
    scoped to, same convention as every other cross-page link in the
    app). Returns ``{"has_data": False}`` for no rows. Income/expense
    plot as magnitudes (``abs``) rising from the shared zero baseline —
    mixing a signed expense total with an unsigned bar height would read
    wrong — while the net worth line plots its actual (signed) value
    against the same baseline. Y-axis ticks start from 0 (see
    ``_tick_bounds``) at a step ``_nice_step`` picks to fit the data
    (``tick_step`` overrides that, mainly for tests that want an exact,
    predictable step rather than whatever the sample data happens to
    produce).

    Each month gets a fixed ``per_month_width`` rather than however many
    months squeezed into one flat total width — the whole chart widens
    with the data instead of the bars getting thinner as more months
    accumulate. The template scrolls this horizontally within a
    ``VISIBLE_MONTHS``-wide viewport rather than showing years of
    history at once, illegibly dense.
    """
    if not rows:
        return {"has_data": False}

    net_worth_values = [float(net_worth) for _, _, net_worth, _, _ in rows]
    magnitudes = [abs(float(income)) for _, _, _, income, _ in rows] + [
        abs(float(expense)) for _, _, _, _, expense in rows
    ]
    all_values = net_worth_values + magnitudes
    step = (
        tick_step
        if tick_step is not None
        else _nice_step(max((abs(v) for v in all_values), default=0))
    )
    y_min, y_max = _tick_bounds(all_values, step)

    count = len(rows)
    plot_left = pad_left
    group_width = per_month_width
    width = pad_left + group_width * count + pad_right
    plot_right = width - pad_right
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    bar_width = group_width * 0.32
    gap = group_width * 0.06

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    zero_y = y_at(0)

    bars = []
    coords = []
    x_labels = []
    previous_year = None
    for index, (key, label, net_worth, income, expense) in enumerate(rows):
        center = plot_left + group_width * (index + 0.5)
        income_y = y_at(abs(float(income)))
        expense_y = y_at(abs(float(expense)))
        month_date_from, month_date_to = _month_date_bounds(key)
        bars.append(
            {
                "income_x": center - gap / 2 - bar_width,
                "income_y": income_y,
                "income_h": zero_y - income_y,
                "income_label": f"{abs(income):,.2f}",
                "income_link": breadcrumbs.transactions_link(
                    date_from=month_date_from,
                    date_to=month_date_to,
                    txn_type=TransactionType.INCOME.value,
                    account_id=account_id,
                ),
                "expense_x": center + gap / 2,
                "expense_y": expense_y,
                "expense_h": zero_y - expense_y,
                "expense_label": f"{abs(expense):,.2f}",
                "expense_link": breadcrumbs.transactions_link(
                    date_from=month_date_from,
                    date_to=month_date_to,
                    txn_type=TransactionType.EXPENSE.value,
                    account_id=account_id,
                ),
                "bar_width": bar_width,
                "label": label,
            }
        )
        coords.append((center, y_at(float(net_worth))))

        year, month = key.split("-")
        x_labels.append(
            {
                "x": center,
                "month": _MONTH_ABBR[int(month)],
                # Year shown only where it changes (or the very first
                # column) — repeating it under every single month would
                # just be noise once 12+ months are on screen at once.
                "year": year if year != previous_year else "",
            }
        )
        previous_year = year

    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)

    step_count = round((y_max - y_min) / step)
    y_ticks = [
        {
            "y": y_at(y_min + i * step),
            "label": f"{y_min + i * step:,.0f}",
        }
        for i in range(step_count + 1)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "zero_y": zero_y,
        "path_d": path_d,
        "coords": coords,
        "bars": bars,
        "x_labels": x_labels,
        "labels": [label for _, label, _, _, _ in rows],
        "net_worth_labels": [
            f"{float(net_worth):,.2f}" for _, _, net_worth, _, _ in rows
        ],
        "y_ticks": y_ticks,
        "visible_width": pad_left + group_width * min(count, VISIBLE_MONTHS),
        # When the data already fits within one screen (a single year on
        # reports/year.html, or a short history on reports/list.html),
        # there's nothing to scroll to — the template instead lets the
        # chart stretch to fill whatever width its container actually
        # has (CSS width: 100%), rather than sitting at its native,
        # possibly much narrower, pixel size with empty space beside it.
        # Once there's more than VISIBLE_MONTHS of data the fixed
        # per-month pixel width has to hold instead, so the scroll
        # container's width means something concrete.
        "needs_scroll": count > VISIBLE_MONTHS,
    }


def _svg_category_chart(
    points: list[CategoryMonthPoint],
    *,
    budget: Decimal | None,
    txn_type: TransactionType | None = None,
    category: str = "",
    subcategory: str = "",
    account_id: str = "",
    per_month_width: int = PER_MONTH_W,
    height: int = 380,
    pad_left: int = 84,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 42,
    tick_step: float | None = None,
) -> dict:
    """Build one category's monthly-trend chart geometry (``/reports/category``).

    Same fixed-per-month-width, scrolling, pinned-axis mechanic as
    ``_svg_net_worth_chart``, simplified to a single bar series — one
    category's monthly totals are one magnitude, not an income/expense
    pair sharing an axis. When ``budget`` is set (expense categories
    only — income categories never carry one, see
    ``app.models.category``), a bar's portion above the budget line
    renders as a second, more transparent rect of the *same* fill color
    rather than a different hue: the bar's color is the mark's identity
    (this category, this transaction type), so "over budget" is encoded
    as an emphasis on the overflow segment instead of a second, colliding
    color meaning. When ``txn_type``/``category`` are given (the normal
    case — left unset only by tests that don't care about the links),
    each bar also gets its own click-through ``link`` into
    ``/transactions``, scoped to that exact month — same "bars are
    clickable" convention ``_svg_net_worth_chart`` already established.
    """
    if not points:
        return {"has_data": False}

    totals = [float(p.total) for p in points]
    all_values = totals + ([float(budget)] if budget else [])
    step = (
        tick_step if tick_step is not None else _nice_step(max(all_values, default=0))
    )
    y_min, y_max = _tick_bounds(all_values, step)

    count = len(points)
    plot_left = pad_left
    width = pad_left + per_month_width * count + pad_right
    plot_right = width - pad_right
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    bar_width = per_month_width * 0.5

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    zero_y = y_at(0)
    budget_value = float(budget) if budget else None
    budget_y = y_at(budget_value) if budget_value is not None else None

    bars = []
    x_labels = []
    previous_year = None
    for index, point in enumerate(points):
        center = plot_left + per_month_width * (index + 0.5)
        total = float(point.total)
        total_y = y_at(total)
        if budget_value is not None and total > budget_value:
            segments = [
                {"y": budget_y, "height": zero_y - budget_y, "over": False},
                {"y": total_y, "height": budget_y - total_y, "over": True},
            ]
        else:
            segments = [{"y": total_y, "height": zero_y - total_y, "over": False}]
        month_date_from, month_date_to = _month_date_bounds(point.key)
        bars.append(
            {
                "x": center - bar_width / 2,
                "bar_width": bar_width,
                "segments": segments,
                "label": point.label,
                "total_label": f"{point.total:,.2f}",
                "count": point.count,
                "link": (
                    breadcrumbs.transactions_link(
                        date_from=month_date_from,
                        date_to=month_date_to,
                        category=category,
                        subcategory=subcategory,
                        txn_type=txn_type.value,
                        account_id=account_id,
                    )
                    if txn_type is not None and category
                    else None
                ),
            }
        )
        year, month = point.key.split("-")
        x_labels.append(
            {
                "x": center,
                "month": _MONTH_ABBR[int(month)],
                "year": year if year != previous_year else "",
            }
        )
        previous_year = year

    step_count = round((y_max - y_min) / step)
    y_ticks = [
        {
            "y": y_at(y_min + i * step),
            "label": f"{y_min + i * step:,.0f}",
        }
        for i in range(step_count + 1)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "bars": bars,
        "x_labels": x_labels,
        "y_ticks": y_ticks,
        "budget_y": budget_y,
        "budget_label": f"Budget {budget:,.2f}/mo" if budget else None,
        "visible_width": pad_left + per_month_width * min(count, VISIBLE_MONTHS),
        "needs_scroll": count > VISIBLE_MONTHS,
    }


def _weekly_points_for_month(
    transactions: list,
    category: str,
    txn_type: TransactionType,
    year: int,
    month: int,
    *,
    subcategory: str | None = None,
) -> tuple[list[CategoryMonthPoint], list[tuple[str, str]]]:
    """Return ``(points, date_ranges)`` — one entry per 7-day chunk of one month.

    Backs the month drill-down's own trend chart: a single month has no
    monthly trend to show, so it's broken into weekly chunks instead
    (days 1-7, 8-14, ... — the last chunk shorter when the month doesn't
    divide evenly by 7). Deliberately not aligned to real Mon-Sun
    calendar weeks — a week spanning two months would pull in
    transactions outside the month actually being viewed. ``date_ranges``
    is each chunk's own ``(first day, last day)`` as ISO strings, parallel
    to ``points``, for the chart's own per-bar click-through links.
    ``subcategory``, when given, further narrows to just that subcategory
    — the subcategory-detail page's own month-scoped chart reuses this
    same weekly-bucketing.
    """
    last_day = monthrange(year, month)[1]
    chunks = []
    start = 1
    while start <= last_day:
        end = min(start + 6, last_day)
        chunks.append((start, end))
        start = end + 1

    points = []
    date_ranges = []
    for start_day, end_day in chunks:
        matching = [
            t
            for t in transactions
            if t.type is txn_type
            and t.category == category
            and (subcategory is None or t.subcategory == subcategory)
            and t.date.year == year
            and t.date.month == month
            and start_day <= t.date.day <= end_day
        ]
        total = sum((abs(t.amount) for t in matching), Decimal("0"))
        label = f"{start_day}–{end_day}" if end_day != start_day else f"{start_day}"
        points.append(
            CategoryMonthPoint(
                key=f"{year:04d}-{month:02d}-{start_day:02d}",
                label=label,
                total=total,
                count=len(matching),
            )
        )
        date_ranges.append(
            (
                f"{year:04d}-{month:02d}-{start_day:02d}",
                f"{year:04d}-{month:02d}-{end_day:02d}",
            )
        )
    return points, date_ranges


def _svg_category_week_chart(
    points: list[CategoryMonthPoint],
    date_ranges: list[tuple[str, str]],
    *,
    txn_type: TransactionType,
    category: str,
    subcategory: str = "",
    account_id: str = "",
    width: int = 640,
    height: int = 260,
    pad_left: int = 76,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 36,
    tick_step: float | None = None,
) -> dict:
    """Build one month's weekly-bucketed trend chart geometry.

    Unlike ``_svg_category_chart``'s fixed-per-month-width/scrolling
    mechanic (built for many months), a single month only ever has 4-5
    weekly buckets (see ``_weekly_points_for_month``), so this always
    fits one fixed-size chart with no scrolling needed — same "stretch
    to fill the container" treatment ``_svg_net_worth_chart`` uses for a
    short history. No budget-overflow segment split either: a monthly
    budget has no single-week equivalent to compare one bucket against.
    """
    if not points:
        return {"has_data": False}

    totals = [float(p.total) for p in points]
    step = tick_step if tick_step is not None else _nice_step(max(totals, default=0))
    y_min, y_max = _tick_bounds(totals, step)

    count = len(points)
    plot_left = pad_left
    plot_right = width - pad_right
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    group_width = (plot_right - plot_left) / count
    bar_width = group_width * 0.5

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    zero_y = y_at(0)
    bars = []
    for index, point in enumerate(points):
        center = plot_left + group_width * (index + 0.5)
        total_y = y_at(float(point.total))
        date_from, date_to = date_ranges[index]
        bars.append(
            {
                "x": center - bar_width / 2,
                "bar_width": bar_width,
                "y": total_y,
                "height": zero_y - total_y,
                "label": point.label,
                "total_label": f"{point.total:,.2f}",
                "count": point.count,
                "link": breadcrumbs.transactions_link(
                    date_from=date_from,
                    date_to=date_to,
                    category=category,
                    subcategory=subcategory,
                    txn_type=txn_type.value,
                    account_id=account_id,
                ),
            }
        )

    step_count = round((y_max - y_min) / step)
    y_ticks = [
        {"y": y_at(y_min + i * step), "label": f"{y_min + i * step:,.0f}"}
        for i in range(step_count + 1)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "bars": bars,
        "y_ticks": y_ticks,
    }


_WEEKLY_SUBCATEGORY_LIMIT = 9


def _weekly_subcategory_points_for_month(
    transactions: list,
    category: str,
    txn_type: TransactionType,
    year: int,
    month: int,
) -> tuple[
    list[CategoryMonthPoint], list[SubcategoryMonthPoint], list[tuple[str, str]]
]:
    """Return ``(points, series, date_ranges)`` — one month's subcategory stack, weekly.

    The weekly counterpart to ``category_subcategory_monthly_series``,
    scoped to a single month (same 7-day chunking as
    ``_weekly_points_for_month``) — ranked by each subcategory's whole-
    month total (not re-ranked per week) and collapsed past
    ``_WEEKLY_SUBCATEGORY_LIMIT`` into one "Other" entry, same top-N-
    plus-"Other" convention (a blank subcategory folds into "Other" too)
    so a subcategory's color still means the same thing whether it's
    looked up here or on the year-long stacked chart.
    """
    last_day = monthrange(year, month)[1]
    chunks = []
    start = 1
    while start <= last_day:
        end = min(start + 6, last_day)
        chunks.append((start, end))
        start = end + 1

    month_txns = [
        t
        for t in transactions
        if t.type is txn_type
        and t.category == category
        and t.date.year == year
        and t.date.month == month
    ]

    sub_totals: dict[str, Decimal] = {}
    for t in month_txns:
        name = t.subcategory or ""
        sub_totals[name] = sub_totals.get(name, Decimal("0")) + abs(t.amount)
    ranked_names = [
        name
        for name, _ in sorted(sub_totals.items(), key=lambda kv: kv[1], reverse=True)
        if name
    ]
    top_names = ranked_names[:_WEEKLY_SUBCATEGORY_LIMIT]
    other_names = set(ranked_names[_WEEKLY_SUBCATEGORY_LIMIT:]) | (
        {""} if "" in sub_totals else set()
    )

    points: list[CategoryMonthPoint] = []
    date_ranges: list[tuple[str, str]] = []
    top_totals: dict[str, list[Decimal]] = {name: [] for name in top_names}
    other_totals: list[Decimal] = []
    for start_day, end_day in chunks:
        chunk_txns = [t for t in month_txns if start_day <= t.date.day <= end_day]
        total = sum((abs(t.amount) for t in chunk_txns), Decimal("0"))
        label = f"{start_day}–{end_day}" if end_day != start_day else f"{start_day}"
        points.append(
            CategoryMonthPoint(
                key=f"{year:04d}-{month:02d}-{start_day:02d}",
                label=label,
                total=total,
                count=len(chunk_txns),
            )
        )
        date_ranges.append(
            (
                f"{year:04d}-{month:02d}-{start_day:02d}",
                f"{year:04d}-{month:02d}-{end_day:02d}",
            )
        )
        for name in top_names:
            top_totals[name].append(
                sum(
                    (abs(t.amount) for t in chunk_txns if t.subcategory == name),
                    Decimal("0"),
                )
            )
        other_totals.append(
            sum(
                (
                    abs(t.amount)
                    for t in chunk_txns
                    if (t.subcategory or "") in other_names
                ),
                Decimal("0"),
            )
        )

    series = [
        SubcategoryMonthPoint(name=name, totals=top_totals[name]) for name in top_names
    ]
    if any(other_totals):
        series.append(SubcategoryMonthPoint(name="Other", totals=other_totals))
    return points, series, date_ranges


def _svg_subcategory_week_stack_chart(
    points: list[CategoryMonthPoint],
    series: list[SubcategoryMonthPoint],
    date_ranges: list[tuple[str, str]],
    *,
    txn_type: TransactionType,
    category: str,
    account_id: str = "",
    width: int = 640,
    height: int = 260,
    pad_left: int = 76,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 36,
    tick_step: float | None = None,
) -> dict:
    """Build one month's subcategory stack, broken into weekly chunks.

    Same simple fixed-size (no scrolling) geometry as
    ``_svg_category_week_chart``, but each bar is a stack of segments
    like ``_svg_subcategory_stack_chart`` — the weekly counterpart to
    that chart, for the same reason ``_svg_category_week_chart`` exists
    instead of reusing the monthly one: a single month has no monthly
    trend to show. No legend filter checkboxes here (unlike the
    year-long chart) — a month only ever has a handful of weeks, so
    there's little need to narrow what's shown.
    """
    if not points or not series:
        return {"has_data": False, "legend": []}

    count = len(points)
    stack_totals = [
        sum((s.totals[i] for s in series), Decimal("0")) for i in range(count)
    ]
    step = (
        tick_step
        if tick_step is not None
        else _nice_step(max((float(v) for v in stack_totals), default=0))
    )
    y_min, y_max = _tick_bounds([float(v) for v in stack_totals], step)

    plot_left = pad_left
    plot_right = width - pad_right
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    group_width = (plot_right - plot_left) / count
    bar_width = group_width * 0.5

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    bars = []
    for index, point in enumerate(points):
        center = plot_left + group_width * (index + 0.5)
        running = Decimal("0")
        segments = []
        date_from, date_to = date_ranges[index]
        for series_index, one_series in enumerate(series):
            amount = one_series.totals[index]
            if amount <= 0:
                continue
            top_y = y_at(float(running + amount))
            bottom_y = y_at(float(running))
            is_other = one_series.name == "Other"
            segments.append(
                {
                    "y": top_y,
                    "height": bottom_y - top_y,
                    "css_class": (
                        "pie-slice-other"
                        if is_other
                        else f"pie-slice-{series_index % 10}"
                    ),
                    "name": one_series.name,
                    "amount_label": f"{amount:,.2f}",
                    "link": breadcrumbs.transactions_link(
                        date_from=date_from,
                        date_to=date_to,
                        category=category,
                        subcategory="" if is_other else one_series.name,
                        txn_type=txn_type.value,
                        account_id=account_id,
                    ),
                }
            )
            running += amount
        bars.append(
            {
                "x": center - bar_width / 2,
                "bar_width": bar_width,
                "segments": segments,
                "label": point.label,
            }
        )

    step_count = round((y_max - y_min) / step)
    y_ticks = [
        {"y": y_at(y_min + i * step), "label": f"{y_min + i * step:,.0f}"}
        for i in range(step_count + 1)
    ]

    legend = [
        {
            "name": one_series.name,
            "css_class": (
                "pie-slice-other"
                if one_series.name == "Other"
                else f"pie-slice-{series_index % 10}"
            ),
            "total": sum(one_series.totals, Decimal("0")),
        }
        for series_index, one_series in enumerate(series)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "bars": bars,
        "y_ticks": y_ticks,
        "legend": legend,
    }


def _svg_subcategory_stack_chart(
    months: list[CategoryMonthPoint],
    series: list[SubcategoryMonthPoint],
    *,
    visible: set[str] | None = None,
    txn_type: TransactionType | None = None,
    category: str = "",
    account_id: str = "",
    per_month_width: int = PER_MONTH_W,
    height: int = 380,
    pad_left: int = 84,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 42,
    tick_step: float | None = None,
) -> dict:
    """Build a category's monthly trend stacked by subcategory.

    Same fixed-per-month-width, scrolling, pinned-axis mechanic as
    ``_svg_category_chart``, but each bar is a stack of one segment per
    visible ``series`` entry instead of a single fill — colored via a
    fixed ``pie-slice-N``/``pie-slice-other`` categorical palette
    (fixed order, "Other" always a muted gray), the same one the
    month drill-down's spending pie chart used before it was replaced
    by the expense-category treemap, so a subcategory's color still
    means the same thing wherever it's looked up. No budget line:
    unlike ``_svg_category_chart``, there's no single figure here to
    compare a *stack* against — a category's own budget is checked
    against its combined total, not any one subcategory's slice of it.

    ``visible`` (a set of subcategory names) backs the legend's own
    filter checkboxes — ``None`` means "everything" (every subcategory in
    ``series``). A hidden subcategory's segments are left out of the
    stack entirely and the y-axis rescales to whatever's actually
    visible, rather than staying pinned to the full, unfiltered range —
    filtering down to one subcategory should let you read its own trend
    at a readable scale, not as a sliver against the old full-stack
    height. Colors are still assigned from each entry's position in the
    *full* ``series`` list, never the filtered one, so hiding one
    subcategory never recolors the ones still shown — color is the
    mark's identity and must survive a filter that changes which marks
    are drawn (see the dataviz "color follows the entity, never its
    rank" rule). Returns ``{"has_data": False, "legend": []}`` for no
    months/series (the caller — a category with no subcategorized
    activity at all — already skips calling this). When ``txn_type``/
    ``category`` are given, each segment also gets its own click-through
    ``link`` into ``/transactions``, scoped to that exact month and
    subcategory — "Other" segments link to just the month (no single
    subcategory to filter by, since "Other" is several collapsed
    together) rather than nothing.
    """
    if not months or not series:
        return {"has_data": False, "legend": []}

    visible_names = visible if visible is not None else {s.name for s in series}
    month_count = len(months)
    stack_totals = [
        sum((s.totals[i] for s in series if s.name in visible_names), Decimal("0"))
        for i in range(month_count)
    ]
    step = (
        tick_step
        if tick_step is not None
        else _nice_step(max((float(v) for v in stack_totals), default=0))
    )
    y_min, y_max = _tick_bounds([float(v) for v in stack_totals], step)

    plot_left = pad_left
    width = pad_left + per_month_width * month_count + pad_right
    plot_right = width - pad_right
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    bar_width = per_month_width * 0.5

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    bars = []
    x_labels = []
    previous_year = None
    for index, month in enumerate(months):
        center = plot_left + per_month_width * (index + 0.5)
        running = Decimal("0")
        segments = []
        month_date_from, month_date_to = _month_date_bounds(month.key)
        for series_index, one_series in enumerate(series):
            if one_series.name not in visible_names:
                continue
            amount = one_series.totals[index]
            if amount <= 0:
                continue
            top_y = y_at(float(running + amount))
            bottom_y = y_at(float(running))
            is_other = one_series.name == "Other"
            segments.append(
                {
                    "y": top_y,
                    "height": bottom_y - top_y,
                    "css_class": (
                        "pie-slice-other"
                        if is_other
                        else f"pie-slice-{series_index % 10}"
                    ),
                    "name": one_series.name,
                    "amount_label": f"{amount:,.2f}",
                    "link": (
                        breadcrumbs.transactions_link(
                            date_from=month_date_from,
                            date_to=month_date_to,
                            category=category,
                            subcategory="" if is_other else one_series.name,
                            txn_type=txn_type.value,
                            account_id=account_id,
                        )
                        if txn_type is not None and category
                        else None
                    ),
                }
            )
            running += amount
        bars.append(
            {
                "x": center - bar_width / 2,
                "bar_width": bar_width,
                "segments": segments,
                "label": month.label,
                "total_label": f"{stack_totals[index]:,.2f}",
            }
        )
        year, month_num = month.key.split("-")
        x_labels.append(
            {
                "x": center,
                "month": _MONTH_ABBR[int(month_num)],
                "year": year if year != previous_year else "",
            }
        )
        previous_year = year

    step_count = round((y_max - y_min) / step)
    y_ticks = [
        {"y": y_at(y_min + i * step), "label": f"{y_min + i * step:,.0f}"}
        for i in range(step_count + 1)
    ]

    legend = [
        {
            "name": one_series.name,
            "css_class": (
                "pie-slice-other"
                if one_series.name == "Other"
                else f"pie-slice-{series_index % 10}"
            ),
            "total": sum(one_series.totals, Decimal("0")),
            "visible": one_series.name in visible_names,
        }
        for series_index, one_series in enumerate(series)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "bars": bars,
        "x_labels": x_labels,
        "y_ticks": y_ticks,
        "legend": legend,
        "visible_width": pad_left + per_month_width * min(month_count, VISIBLE_MONTHS),
        "needs_scroll": month_count > VISIBLE_MONTHS,
    }


def _filter_by_account(transactions: list, account_id: str) -> list:
    """Filter the ledger to one account before aggregating, or return it unchanged.

    Same "filter before aggregating" convention as the transactions
    list's own account filter (CLAUDE.md) — every figure on a report
    page should reflect just the filtered account's activity, not the
    whole ledger with irrelevant rows hidden after the fact. Transfer
    legs are kept, not excluded — a transfer leg still moves money into
    or out of the filtered account, so it belongs in that account's own
    net worth/income/expense figures even though it nets to zero
    combined across every account.
    """
    if not account_id:
        return transactions
    return [t for t in transactions if t.account_id == account_id]


def _net_worth_chart(
    transactions: list, accounts: list, account_id: str, *, year: int | None = None
) -> dict:
    """Build ``_svg_net_worth_chart``'s geometry, optionally scoped to one year.

    Shared by ``reports_overview`` (the full history) and ``year_detail``
    (just that year's slice) so the two never duplicate the same
    "resolve which accounts' starting balances count, run
    ``net_worth_by_month``, zip in each month's income/expense" steps.
    Net worth is always computed from the *full* transaction history
    even when ``year`` narrows the result afterward — it's a running
    cumulative total, so computing it against only one year's
    transactions would drop every prior year's contribution and start
    the line from zero instead of where the account actually stood
    entering that year.
    """
    net_worth_accounts = (
        [a for a in accounts if a.id == account_id] if account_id else accounts
    )
    net_worth_points = net_worth_by_month(transactions, net_worth_accounts)
    if year is not None:
        net_worth_points = [
            p for p in net_worth_points if p.key.startswith(f"{year:04d}-")
        ]
    monthly_totals = {
        month.key: month for month in monthly_totals_with_mom(transactions)
    }
    return _svg_net_worth_chart(
        [
            (
                point.key,
                point.label,
                point.value,
                monthly_totals[point.key].income_total,
                monthly_totals[point.key].expense_total,
            )
            for point in net_worth_points
        ],
        account_id=account_id,
    )


def _year_stats(years: list, today: date) -> dict[int, dict]:
    """Return per-year ``{"avg_per_month", "savings_rate"}`` for the annual table.

    ``avg_per_month`` divides by the months actually elapsed for the
    current, still-in-progress year (so it reads as a monthly run rate,
    not diluted by months that haven't happened yet) and by 12 for every
    completed year.
    """
    stats = {}
    for y in years:
        months_elapsed = today.month if y.year == today.year else 12
        stats[y.year] = {
            "avg_per_month": y.net_total / months_elapsed,
            "savings_rate": _savings_rate(y.net_total, y.income_total),
        }
    return stats


def _normalize_treemap_sizes(sizes: list[float], dx: float, dy: float) -> list[float]:
    """Scale ``sizes`` to sum to exactly ``dx * dy`` — squarify's input contract."""
    total = sum(sizes)
    if total <= 0:
        return [0.0] * len(sizes)
    factor = dx * dy / total
    return [s * factor for s in sizes]


def _treemap_layout_row(
    sizes: list[float], x: float, y: float, dy: float
) -> list[tuple[float, float, float, float]]:
    covered = sum(sizes)
    width = covered / dy if dy else 0.0
    rects = []
    cy = y
    for size in sizes:
        h = size / width if width else 0.0
        rects.append((x, cy, width, h))
        cy += h
    return rects


def _treemap_layout_col(
    sizes: list[float], x: float, y: float, dx: float
) -> list[tuple[float, float, float, float]]:
    covered = sum(sizes)
    height = covered / dx if dx else 0.0
    rects = []
    cx = x
    for size in sizes:
        w = size / height if height else 0.0
        rects.append((cx, y, w, height))
        cx += w
    return rects


def _treemap_layout(
    sizes: list[float], x: float, y: float, dx: float, dy: float
) -> list[tuple[float, float, float, float]]:
    return (
        _treemap_layout_row(sizes, x, y, dy)
        if dx >= dy
        else _treemap_layout_col(sizes, x, y, dx)
    )


def _treemap_leftover(
    sizes: list[float], x: float, y: float, dx: float, dy: float
) -> tuple[float, float, float, float]:
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy if dy else 0.0
        return (x + width, y, dx - width, dy)
    height = covered / dx if dx else 0.0
    return (x, y + height, dx, dy - height)


def _treemap_worst_ratio(
    sizes: list[float], x: float, y: float, dx: float, dy: float
) -> float:
    worst = 0.0
    for _, _, w, h in _treemap_layout(sizes, x, y, dx, dy):
        if w <= 0 or h <= 0:
            return float("inf")
        worst = max(worst, w / h, h / w)
    return worst if sizes else float("inf")


def _squarify(
    sizes: list[float], x: float, y: float, dx: float, dy: float
) -> list[tuple[float, float, float, float]]:
    """Return one ``(x, y, w, h)`` rect per entry in ``sizes``, tiled to fill the rect.

    A straightforward port of the well-known "squarified treemap"
    algorithm (Bruls, Huizing, van Wijk): greedily grows the current
    row as long as doing so doesn't worsen the row's worst box aspect
    ratio, lays that row out across whichever side of the remaining
    rectangle is shorter, then recurses into whatever rectangle is
    left over. ``sizes`` must already be area-normalized to sum to
    ``dx * dy`` (``_normalize_treemap_sizes``) and read best sorted
    descending, same as every reference implementation — the caller is
    responsible for both.
    """
    if not sizes or dx <= 0 or dy <= 0:
        return []
    if len(sizes) == 1:
        return _treemap_layout(sizes, x, y, dx, dy)

    i = 1
    while i < len(sizes) and _treemap_worst_ratio(
        sizes[:i], x, y, dx, dy
    ) >= _treemap_worst_ratio(sizes[: i + 1], x, y, dx, dy):
        i += 1

    current = sizes[:i]
    remaining = sizes[i:]
    leftover_x, leftover_y, leftover_dx, leftover_dy = _treemap_leftover(
        current, x, y, dx, dy
    )
    return _treemap_layout(current, x, y, dx, dy) + _squarify(
        remaining, leftover_x, leftover_y, leftover_dx, leftover_dy
    )


# The landing page's expense-category treemap — fixed canvas size (the
# viewBox coordinate system every box's x/y/w/h is computed in; the
# template stretches it to the container's actual width via CSS, same
# "no-scroll, scale to fill" convention as a short net-worth chart).
_TREEMAP_WIDTH = 960
_TREEMAP_HEIGHT = 440

# Rough average glyph width (viewBox px, at the label's own 13px bold
# font) used to estimate whether a box is wide enough for its full
# category name — there's no real text-measurement available
# server-side without a font-metrics library, and this is close enough
# to decide "fits" vs. "doesn't" for the box-label/icon-only/blank
# three-way fallback below.
_TREEMAP_CHAR_WIDTH = 7.6
_TREEMAP_LABEL_PAD = 8
_TREEMAP_LABEL_MIN_HEIGHT = 22
_TREEMAP_ICON_SIZE = 15
_TREEMAP_ICON_GAP = 4
# A box too small even for the name can often still fit just the
# category's icon, centered, rather than showing nothing at all.
_TREEMAP_ICON_ONLY_MIN = 20

# A continuous 3-stop gradient (dark green -> amber -> dark red) for
# a box's color, rather than a small set of discrete bands — far more
# visually differentiated across a realistic spread of category shares
# than a handful of fixed tiers could be. _TREEMAP_COLOR_CAP_PCT is the
# % of whole-period total expense at/above which the scale is already
# fully red: a category-level distribution can realistically have one
# claim 20-40%+ of a whole budget (unlike an individual transaction or
# subcategory), so a single category claiming even half of everything
# is already a dominant one — there's no need for headroom past it.
_TREEMAP_GREEN = (27, 94, 54)
_TREEMAP_AMBER = (196, 141, 30)
_TREEMAP_RED = (140, 34, 24)
_TREEMAP_COLOR_CAP_PCT = 50.0


def _lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[str, tuple[int, int, int]]:
    """Linearly interpolate two RGB triples at ``t`` (0-1) — hex string plus raw RGB."""
    r, g, b = (round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return f"#{r:02x}{g:02x}{b:02x}", (r, g, b)


def _treemap_color(pct: float) -> tuple[str, bool]:
    """Return ``(hex color, needs light text)`` for a % of the period's total expense.

    Green at 0%, amber at the midpoint, dark red at/above
    ``_TREEMAP_COLOR_CAP_PCT`` — two linear segments rather than one,
    since a plain green-to-red lerp passes through a muddy brown around
    the middle instead of a clean amber. "Needs light text" is decided
    from the interpolated color's own perceived luminance (ITU-R
    BT.601), not a fixed threshold, since it has to stay correct across
    a continuous range rather than a handful of known swatches.
    """
    t = max(0.0, min(pct, _TREEMAP_COLOR_CAP_PCT)) / _TREEMAP_COLOR_CAP_PCT
    if t <= 0.5:
        color, (r, g, b) = _lerp_color(_TREEMAP_GREEN, _TREEMAP_AMBER, t / 0.5)
    else:
        color, (r, g, b) = _lerp_color(_TREEMAP_AMBER, _TREEMAP_RED, (t - 0.5) / 0.5)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return color, luminance < 0.55


_TREEMAP_SIZE_BY_LABELS = {
    "count": {
        "toggle": "Size: transactions · Color: % of spend",
        "color_label": "% of total expense",
    },
    "total": {
        "toggle": "Size: % of spend · Color: transactions",
        "color_label": "% of all transactions",
    },
}


def _category_treemap(
    categories: list[CategoryTotal],
    expense_config: dict,
    account_id: str,
    grand_total: Decimal,
    *,
    date_from: str = "",
    date_to: str = "",
    size_by: str = "count",
) -> dict | None:
    """Build an expense-category treemap's geometry (``/reports``, ``/reports/{year}``).

    One box per expense category with any activity in the period
    ``categories`` was computed for — every category, not just the
    "Top spending categories" table's own preview above it on the
    landing page, since a treemap can legibly show far more entries
    than a ranked table. Deliberately category-level only, never
    drilling into subcategories: the per-category drill-down with its
    own subcategory breakdown already lives one click away, at
    ``/reports/category``. ``date_from``/``date_to`` (both empty for
    the landing page's own all-time treemap) scope every box's
    click-through link to that same period — the caller is responsible
    for also having scoped ``categories``/``grand_total`` to it.

    A box's *area* and *color* are always driven by the two independent
    metrics this treemap can show — a category's share of the whole
    period's *transaction count*, and its share of the whole period's
    total *expense* — with ``size_by`` picking which one drives area
    (the other drives color, a green-to-red gradient via
    ``_treemap_color``). Default ``"count"`` (area = transaction share,
    color = spend share) reads a rare, large expense — a mortgage
    payment, few transactions, high dollar share — as "small and deep
    red," and a frequent, cheap one (groceries) as "big and however red
    its own dollar share earns." ``"total"`` swaps the two, so area
    instead reads as "how much of my money went here" and color as "how
    often did I even buy this" — see ``_treemap_pair`` for the
    page-level toggle between the two that reuses this same function.
    """
    active = [c for c in categories if c.count > 0]
    if not active:
        return None

    size_key = (
        (lambda c: float(c.total)) if size_by == "total" else (lambda c: float(c.count))
    )
    active = sorted(active, key=size_key, reverse=True)
    grand_count = sum(c.count for c in active)
    sizes = _normalize_treemap_sizes(
        [size_key(c) for c in active], _TREEMAP_WIDTH, _TREEMAP_HEIGHT
    )
    rects = _squarify(sizes, 0, 0, _TREEMAP_WIDTH, _TREEMAP_HEIGHT)

    boxes = []
    for category, (x, y, w, h) in zip(active, rects, strict=True):
        pct = float(category.total / grand_total * 100) if grand_total else 0.0
        txn_pct = float(category.count / grand_count * 100) if grand_count else 0.0
        color, light_text = _treemap_color(txn_pct if size_by == "total" else pct)
        icon = expense_config.get(category.name, {}).get("icon", "")
        icon_scale = _TREEMAP_ICON_SIZE / 24

        required_width = (
            2 * _TREEMAP_LABEL_PAD
            + (_TREEMAP_ICON_SIZE + _TREEMAP_ICON_GAP if icon else 0)
            + len(category.name) * _TREEMAP_CHAR_WIDTH
        )
        show_name = w >= required_width and h >= _TREEMAP_LABEL_MIN_HEIGHT
        # A box too narrow/short for its full name often still has room
        # for just the icon, centered — better than showing nothing.
        show_icon_only = (
            not show_name
            and bool(icon)
            and w >= _TREEMAP_ICON_ONLY_MIN
            and h >= _TREEMAP_ICON_ONLY_MIN
        )
        boxes.append(
            {
                "name": category.name,
                "icon": icon,
                "total": category.total,
                "count": category.count,
                "pct": pct,
                "txn_pct": txn_pct,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "color": color,
                "light_text": light_text,
                "show_name": show_name,
                "show_icon_only": show_icon_only,
                "icon_scale": icon_scale,
                "icon_x": x + _TREEMAP_LABEL_PAD,
                "icon_y": y + _TREEMAP_LABEL_PAD,
                "icon_only_x": x + w / 2 - _TREEMAP_ICON_SIZE / 2,
                "icon_only_y": y + h / 2 - _TREEMAP_ICON_SIZE / 2,
                "name_x": (
                    x
                    + _TREEMAP_LABEL_PAD
                    + (_TREEMAP_ICON_SIZE + _TREEMAP_ICON_GAP if icon else 0)
                ),
                "link": breadcrumbs.transactions_link(
                    category=category.name,
                    txn_type=TransactionType.EXPENSE.value,
                    account_id=account_id,
                    date_from=date_from,
                    date_to=date_to,
                ),
            }
        )

    legend_stops = [
        _treemap_color(0.0)[0],
        _treemap_color(_TREEMAP_COLOR_CAP_PCT / 2)[0],
        _treemap_color(_TREEMAP_COLOR_CAP_PCT)[0],
    ]
    return {
        "width": _TREEMAP_WIDTH,
        "height": _TREEMAP_HEIGHT,
        "boxes": boxes,
        "legend_stops": legend_stops,
        "legend_cap_pct": _TREEMAP_COLOR_CAP_PCT,
        "size_by": size_by,
        "color_label": _TREEMAP_SIZE_BY_LABELS[size_by]["color_label"],
        "toggle_label": _TREEMAP_SIZE_BY_LABELS[size_by]["toggle"],
    }


def _treemap_pair(
    categories: list[CategoryTotal],
    expense_config: dict,
    account_id: str,
    grand_total: Decimal,
    *,
    date_from: str = "",
    date_to: str = "",
) -> dict | None:
    """Build both treemap variants a page's toggle switches between.

    ``by_count`` (area = transaction share, color = spend share, the
    default shown on load) and ``by_total`` (the two swapped) — both
    fully rendered server-side and swapped client-side via a plain
    ``hidden``-attribute toggle, same "pre-rendered blocks, no round
    trip" convention as the category-detail page's own This month/This
    year/All time toggle. ``None`` when there's nothing to show (no
    expense activity in the period), so the caller can skip the whole
    section the same way a single ``_category_treemap`` call already
    lets it.
    """
    by_count = _category_treemap(
        categories,
        expense_config,
        account_id,
        grand_total,
        date_from=date_from,
        date_to=date_to,
        size_by="count",
    )
    if by_count is None:
        return None
    by_total = _category_treemap(
        categories,
        expense_config,
        account_id,
        grand_total,
        date_from=date_from,
        date_to=date_to,
        size_by="total",
    )
    return {"by_count": by_count, "by_total": by_total}


_SPENDING_CATEGORIES_COLUMNS = ["name", "total", "pct", "count"]
_SPENDING_CATEGORIES_DEFAULT_DIR = {
    "name": "asc",
    "total": "desc",
    "pct": "desc",
    "count": "desc",
}
_SPENDING_CATEGORIES_LABELS = {
    "name": "Category",
    "total": "Total",
    "pct": "% of expenses",
    "count": "# Txns",
}


def _spending_categories_sort_link(
    column: str, current_sort: str, current_dir: str, account_id: str
) -> str:
    """Build one column header's sort link for the Top spending categories table.

    Clicking the already-active column flips its direction; clicking
    any other column switches to it at that column's own sensible
    default direction (name: A-Z, every numeric column: highest
    first) — the same "click again to reverse" convention as any
    ordinary sortable table header.
    """
    next_dir = (
        ("asc" if current_dir == "desc" else "desc")
        if column == current_sort
        else _SPENDING_CATEGORIES_DEFAULT_DIR[column]
    )
    query = f"cat_sort={column}&cat_dir={next_dir}"
    if account_id:
        query += f"&account_id={quote(account_id)}"
    return f"/reports?{query}"


def _spending_categories_headers(
    current_sort: str, current_dir: str, account_id: str
) -> list[dict]:
    """Return one header cell per sortable column, in display order."""
    return [
        {
            "column": column,
            "label": _SPENDING_CATEGORIES_LABELS[column],
            "link": _spending_categories_sort_link(
                column, current_sort, current_dir, account_id
            ),
            "arrow": (
                ("▲" if current_dir == "asc" else "▼") if column == current_sort else ""
            ),
        }
        for column in _SPENDING_CATEGORIES_COLUMNS
    ]


@router.get("", response_class=HTMLResponse)
def reports_overview(
    request: Request,
    account_id: str = "",
    cat_sort: str = "total",
    cat_dir: str = "desc",
) -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary.

    ``cat_sort``/``cat_dir`` control the "Top spending categories" table
    only (not the treemap below it, which always sorts by count/color
    independently of this) — plain strings, not enums, so a stale or
    hand-edited link falls back to the default rather than 404ing, same
    convention as ``categories_index``'s own ``sort`` param.
    """
    resolved_sort = cat_sort if cat_sort in _SPENDING_CATEGORIES_COLUMNS else "total"
    resolved_dir = cat_dir if cat_dir in ("asc", "desc") else "desc"

    accounts = read_accounts()
    ledger = read_ledger()
    transactions = _filter_by_account(ledger, account_id)
    categories = read_categories()
    expense_config = _category_config(categories, TransactionType.EXPENSE)
    chart = _net_worth_chart(transactions, accounts, account_id)
    years = yearly_totals_with_yoy(transactions)
    today = date.today()
    year_txn_links = {
        y.year: breadcrumbs.transactions_link(
            date_from=f"{y.year:04d}-01-01",
            date_to=f"{y.year:04d}-12-31",
            account_id=account_id,
        )
        for y in years
    }
    # "Top spending categories" — every expense category's whole-history
    # total (this page is all-time stats only; per-year/YTD figures live
    # on /reports/{year} and the category detail page instead), sortable
    # by any column via cat_sort/cat_dir.
    all_time_expense_breakdown = category_totals_all_time(
        transactions, TransactionType.EXPENSE
    )
    expense_all_time_total = sum(
        (c.total for c in all_time_expense_breakdown), Decimal("0")
    )
    spending_categories = [
        {
            "name": c.name,
            "total": c.total,
            "pct": (
                float(c.total / expense_all_time_total * 100)
                if expense_all_time_total
                else 0.0
            ),
            "count": c.count,
            "link": breadcrumbs.transactions_link(
                category=c.name,
                txn_type=TransactionType.EXPENSE.value,
                account_id=account_id,
            ),
        }
        for c in all_time_expense_breakdown
    ]
    spending_categories.sort(
        key=lambda r: (
            r["name"].lower() if resolved_sort == "name" else r[resolved_sort]
        ),
        reverse=(resolved_dir == "desc"),
    )
    spending_categories_headers = _spending_categories_headers(
        resolved_sort, resolved_dir, account_id
    )
    expense_treemap_pair = _treemap_pair(
        all_time_expense_breakdown, expense_config, account_id, expense_all_time_total
    )

    # All-time hero stats — no delta alongside them, since there's no
    # "previous all-time period" for an all-time total to be compared
    # against (unlike the year/month pages' own YoY/MoM hero tiles).
    all_time_income = sum(
        (t.amount for t in transactions if t.type is TransactionType.INCOME),
        Decimal("0"),
    )
    all_time_expense = sum(
        (t.amount for t in transactions if t.type is TransactionType.EXPENSE),
        Decimal("0"),
    )
    all_time_net = all_time_income + all_time_expense
    all_time_stats = {
        "income_total": all_time_income,
        "expense_total": all_time_expense,
        "net_total": all_time_net,
        "savings_rate": _savings_rate(all_time_net, all_time_income),
    }

    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {
            "rule_windows": rolling_summaries(
                ledger, categories, accounts, today.year, today.month
            ),
            "rule_targets": read_targets(),
            "rule_to_label": f"{month_name[today.month]} {today.year}",
            "years": years,
            "chart": chart,
            "accounts": accounts,
            "account_id": account_id,
            "year_txn_links": year_txn_links,
            "year_stats": _year_stats(years, today),
            "spending_categories": spending_categories,
            "spending_categories_headers": spending_categories_headers,
            "expense_treemap_pair": expense_treemap_pair,
            "expense_config": expense_config,
            "all_time_stats": all_time_stats,
            "current_year": today.year,
        },
    )


def _sparkline_geometry(values: list[Decimal]) -> dict:
    """Return a compact sparkline's line/area path geometry for one category.

    Purely presentational (same "router computes it, template just draws
    it" split as ``_svg_net_worth_chart``) — a 100x28 viewBox, y-axis
    clamped to start at 0 (a total is never negative here, see
    ``category_recent_monthly_totals``) so an all-zero stretch reads as a
    flat line at the bottom rather than wobbling from autoscaling on
    noise. ``has_data`` is ``False`` when every month is zero (nothing to
    meaningfully draw a shape from) or fewer than 2 points exist.
    """
    width, height, pad = 100.0, 28.0, 3.0
    floats = [float(v) for v in values]
    max_value = max(floats) if floats else 0.0
    if max_value <= 0 or len(floats) < 2:
        return {"has_data": False, "width": width, "height": height}

    step = (width - pad * 2) / (len(floats) - 1)
    points = [
        (pad + i * step, pad + (height - pad * 2) * (1 - value / max_value))
        for i, value in enumerate(floats)
    ]
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (
        line
        + f" L {points[-1][0]:.1f},{height - pad:.1f}"
        + f" L {points[0][0]:.1f},{height - pad:.1f} Z"
    )
    return {
        "has_data": True,
        "line": line,
        "area": area,
        "width": width,
        "height": height,
    }


_CATEGORY_INDEX_SORTS = {"growth", "total", "budget"}


@router.get("/categories", response_class=HTMLResponse)
def categories_index(
    request: Request,
    txn_type: str = "expense",
    sort: str = "growth",
    account_id: str = "",
) -> HTMLResponse:
    """Render a sortable index of every category's recent trend and budget status.

    The browsable counterpart to ``/reports/category``, which needs a
    category already in mind — sorting by "growth" (the default) instead
    surfaces savings candidates without one, ranking by the same
    YTD-vs-same-months-last-year delta the landing page's "Biggest
    movers" list uses (``category_movers``). Registered as a static
    ``/categories`` path ahead of ``/{year:int}`` for the same reason
    ``/category`` already is — Starlette's routing never actually
    confuses the two (``int("categories")`` fails cleanly), but keeping
    every non-numeric reports path grouped together above the numeric
    ones documents that they're deliberately distinct siblings.

    ``txn_type``/``sort`` are plain strings, not enums — both are UI
    toggle state read back from a query param, so an invalid value (a
    stale link, a hand-edited URL) falls back to the default rather than
    404ing.
    """
    resolved_type = txn_type if txn_type in ("income", "expense") else "expense"
    type_enum = TransactionType(resolved_type)
    resolved_sort = sort if sort in _CATEGORY_INDEX_SORTS else "growth"

    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    config = _category_config(categories, type_enum)
    today = date.today()

    movers = category_movers(transactions, type_enum, today)
    month_keys, sparklines = category_recent_monthly_totals(
        transactions, type_enum, today
    )
    current_month_totals = {
        cat.name: cat.total
        for cat in category_totals_for_month(
            transactions, today.year, today.month, type_enum
        )
    }
    grand_total = sum((m.ytd_total for m in movers), Decimal("0"))

    rows = []
    for mover in movers:
        cat_config = config.get(mover.name, {"icon": "", "budget": None})
        ring = _ring_geometry(
            current_month_totals.get(mover.name, Decimal("0")), cat_config["budget"]
        )
        rows.append(
            {
                "name": mover.name,
                "icon": cat_config["icon"],
                "sparkline": _sparkline_geometry(
                    sparklines.get(mover.name, [Decimal("0")] * len(month_keys))
                ),
                "ytd_total": mover.ytd_total,
                "pct_of_total": (
                    float(mover.ytd_total / grand_total * 100) if grand_total else 0.0
                ),
                "delta": mover.delta,
                "pct_delta": mover.pct_delta,
                "ring": ring,
                "detail_link": (
                    f"/reports/category?txn_type={resolved_type}"
                    f"&category={quote(mover.name)}"
                    + (f"&account_id={quote(account_id)}" if account_id else "")
                ),
            }
        )

    if resolved_sort == "total":
        rows.sort(key=lambda r: r["ytd_total"], reverse=True)
    elif resolved_sort == "budget":
        rows.sort(
            key=lambda r: (r["ring"] is None, -(r["ring"]["pct"] if r["ring"] else 0))
        )
    else:
        rows.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or Decimal("0"))))

    return templates.TemplateResponse(
        request,
        "reports/categories.html",
        {
            "accounts": accounts,
            "account_id": account_id,
            "txn_type": resolved_type,
            "sort": resolved_sort,
            "rows": rows,
            "sparkline_month_count": len(month_keys),
            "breadcrumbs": breadcrumbs.for_categories_index(account_id),
        },
    )


_CATEGORY_BREAKDOWN_SORTS = {"total", "change"}


def _sort_by_change(breakdown: list[CategoryTotal]) -> list[CategoryTotal]:
    """Re-sort a category breakdown by the size of its YoY change, biggest first.

    Ranks by ``abs(yoy_delta)`` — a category that fell just as hard as
    another rose is just as worth noticing — not signed, so growth and
    shrinkage are ranked on the same scale rather than growth always
    sorting above shrinkage. Categories with no prior-year data to
    compare against (``yoy_delta`` is ``None``) sort last, same
    "nothing to rank them by" convention as ``category_movers``.
    """
    return sorted(
        breakdown,
        key=lambda cat: (cat.yoy_delta is None, -abs(cat.yoy_delta or Decimal("0"))),
    )


@router.get("/{year:int}", response_class=HTMLResponse)
def year_detail(
    request: Request, year: int, account_id: str = "", sort: str = "total"
) -> HTMLResponse:
    """Render one year's monthly breakdown and income/expense category drill-down.

    The path uses an explicit ``{year:int}`` converter (not just a plain
    ``{year}``, which Starlette matches against *any* single path
    segment before FastAPI's own ``year: int`` coercion ever runs) so a
    static sibling route like ``/reports/category`` can never be
    swallowed here and turned into a spurious 422.

    ``sort`` ("total", the default, or "change") reorders both the
    income/expense breakdown tables *and* the month-to-month matrix
    tables below them, which deliberately reuse the breakdown's own
    order (see ``_category_month_matrix``) so the two keep listing
    categories the same way. An unrecognized value falls back to
    "total" rather than 404ing, same "UI toggle state, not a hard
    error" treatment as the categories index page's own ``sort``.
    """
    resolved_sort = sort if sort in _CATEGORY_BREAKDOWN_SORTS else "total"
    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    chart = _net_worth_chart(transactions, accounts, account_id, year=year)
    income_breakdown = category_breakdown(transactions, year, TransactionType.INCOME)
    expense_breakdown = category_breakdown(transactions, year, TransactionType.EXPENSE)
    if resolved_sort == "change":
        income_breakdown = _sort_by_change(income_breakdown)
        expense_breakdown = _sort_by_change(expense_breakdown)
    months = [
        month
        for month in monthly_totals_with_mom(transactions)
        if month.key.startswith(f"{year:04d}-")
    ]
    # Oldest first (``months`` itself is newest first) so the
    # month-to-month table reads left-to-right chronologically.
    month_keys = [month.key for month in reversed(months)]
    month_labels = [month_abbr[int(key.split("-")[1])] for key in month_keys]

    income_monthly = category_monthly_totals(transactions, year, TransactionType.INCOME)
    expense_monthly = category_monthly_totals(
        transactions, year, TransactionType.EXPENSE
    )
    income_sub_monthly = subcategory_monthly_totals(
        transactions, year, TransactionType.INCOME
    )
    expense_sub_monthly = subcategory_monthly_totals(
        transactions, year, TransactionType.EXPENSE
    )
    income_config = _category_config(categories, TransactionType.INCOME)
    expense_config = _category_config(categories, TransactionType.EXPENSE)
    # Backs the breakdown/month-matrix tables' click-through to
    # /transactions, filtered to the clicked category/subcategory plus
    # this exact time span — the whole year for the annual breakdown
    # table, one specific month per cell in the month-to-month matrix.
    month_bounds = {key: _month_date_bounds(key) for key in month_keys}
    month_txn_links = {
        key: breadcrumbs.transactions_link(
            date_from=bounds[0], date_to=bounds[1], account_id=account_id
        )
        for key, bounds in month_bounds.items()
    }
    year_txn_link = breadcrumbs.transactions_link(
        date_from=f"{year:04d}-01-01",
        date_to=f"{year:04d}-12-31",
        account_id=account_id,
    )

    # Hero stat tiles: this year's income/expense/net/savings-rate vs.
    # the immediately preceding year — the plain full-year-vs-full-year
    # comparison yearly_totals_with_yoy already computes (not adjusted
    # for an in-progress year, same simplification the landing page's
    # own annual table already accepts), rather than a YTD-vs-same-
    # months variant — this page is for browsing any single year,
    # including old, fully-completed ones, not just the current one.
    years_by_year = {y.year: y for y in yearly_totals_with_yoy(transactions)}
    this_year_totals = years_by_year.get(year)
    prev_year_totals = years_by_year.get(year - 1)
    year_income_total = (
        this_year_totals.income_total if this_year_totals else Decimal("0")
    )
    year_expense_total = (
        this_year_totals.expense_total if this_year_totals else Decimal("0")
    )
    year_net_total = this_year_totals.net_total if this_year_totals else Decimal("0")
    year_savings_rate = _savings_rate(year_net_total, year_income_total)
    prev_year_savings_rate = (
        _savings_rate(prev_year_totals.net_total, prev_year_totals.income_total)
        if prev_year_totals
        else None
    )
    year_stats = {
        "income_total": year_income_total,
        "expense_total": year_expense_total,
        "net_total": year_net_total,
        "savings_rate": year_savings_rate,
        "income_delta": (
            year_income_total - prev_year_totals.income_total
            if prev_year_totals
            else None
        ),
        "expense_delta": (
            year_expense_total - prev_year_totals.expense_total
            if prev_year_totals
            else None
        ),
        "net_delta": (
            year_net_total - prev_year_totals.net_total if prev_year_totals else None
        ),
        "savings_rate_delta": (
            year_savings_rate - prev_year_savings_rate
            if year_savings_rate is not None and prev_year_savings_rate is not None
            else None
        ),
    }

    expense_year_total = sum((c.total for c in expense_breakdown), Decimal("0"))
    expense_treemap_pair = _treemap_pair(
        expense_breakdown,
        expense_config,
        account_id,
        expense_year_total,
        date_from=f"{year:04d}-01-01",
        date_to=f"{year:04d}-12-31",
    )

    return templates.TemplateResponse(
        request,
        "reports/year.html",
        {
            "year": year,
            "year_stats": year_stats,
            "accounts": accounts,
            "account_id": account_id,
            "chart": chart,
            "months": months,
            "month_labels": month_labels,
            "date_from": f"{year:04d}-01-01",
            "date_to": f"{year:04d}-12-31",
            "month_bounds": month_bounds,
            "month_txn_links": month_txn_links,
            "year_txn_link": year_txn_link,
            "breadcrumbs": breadcrumbs.for_year(year, account_id),
            "sort": resolved_sort,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "income_config": income_config,
            "expense_config": expense_config,
            "income_month_rows": _category_month_matrix(
                income_breakdown,
                income_monthly,
                income_sub_monthly,
                income_config,
                month_keys,
            ),
            "expense_month_rows": _category_month_matrix(
                expense_breakdown,
                expense_monthly,
                expense_sub_monthly,
                expense_config,
                month_keys,
            ),
            "expense_treemap_pair": expense_treemap_pair,
        },
    )


def _adjacent_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return the ``(year, month)`` one step before/after, wrapping years.

    ``delta`` is ``+1`` or ``-1``.

    Backs the month drill-down's Previous/Next navigation — no bounds
    checking against what data actually exists (the page already renders
    a graceful "No data" state for an empty month, so an adjacent link
    can always be shown rather than needing to know in advance whether
    it leads anywhere).
    """
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


@router.get("/{year:int}/{month:int}", response_class=HTMLResponse)
def month_detail(
    request: Request, year: int, month: int, account_id: str = ""
) -> HTMLResponse:
    """Render one month's spending breakdown and income/expense category breakdown."""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=404, detail="Invalid month")

    accounts = read_accounts()
    ledger = read_ledger()
    transactions = _filter_by_account(ledger, account_id)
    categories = read_categories()
    expense_breakdown = category_totals_for_month(
        transactions, year, month, TransactionType.EXPENSE
    )
    income_breakdown = category_totals_for_month(
        transactions, year, month, TransactionType.INCOME
    )
    income_config = _category_config(categories, TransactionType.INCOME)
    expense_config = _category_config(categories, TransactionType.EXPENSE)
    prev_year, prev_month = _adjacent_month(year, month, -1)
    next_year, next_month = _adjacent_month(year, month, 1)
    month_date_from, month_date_to = _month_date_bounds(f"{year:04d}-{month:02d}")
    month_txn_link = breadcrumbs.transactions_link(
        date_from=month_date_from, date_to=month_date_to, account_id=account_id
    )
    expense_month_total = sum((c.total for c in expense_breakdown), Decimal("0"))
    expense_treemap_pair = _treemap_pair(
        expense_breakdown,
        expense_config,
        account_id,
        expense_month_total,
        date_from=month_date_from,
        date_to=month_date_to,
    )

    by_key = {m.key: m for m in monthly_totals_with_mom(transactions)}
    zero_month = {
        "income_total": Decimal("0"),
        "expense_total": Decimal("0"),
        "net_total": Decimal("0"),
    }
    current_month = by_key.get(f"{year:04d}-{month:02d}")
    previous_month = by_key.get(f"{prev_year:04d}-{prev_month:02d}")
    current_stats = (
        {
            "income_total": current_month.income_total,
            "expense_total": current_month.expense_total,
            "net_total": current_month.net_total,
        }
        if current_month
        else zero_month
    )
    previous_stats = (
        {
            "income_total": previous_month.income_total,
            "expense_total": previous_month.expense_total,
            "net_total": previous_month.net_total,
        }
        if previous_month
        else zero_month
    )
    current_savings_rate = _savings_rate(
        current_stats["net_total"], current_stats["income_total"]
    )
    previous_savings_rate = _savings_rate(
        previous_stats["net_total"], previous_stats["income_total"]
    )
    month_stats = {
        "income_total": current_stats["income_total"],
        "expense_total": current_stats["expense_total"],
        "net_total": current_stats["net_total"],
        "savings_rate": current_savings_rate,
        "income_delta": (
            current_stats["income_total"] - previous_stats["income_total"]
            if previous_month
            else None
        ),
        "expense_delta": (
            current_stats["expense_total"] - previous_stats["expense_total"]
            if previous_month
            else None
        ),
        "net_delta": (
            current_stats["net_total"] - previous_stats["net_total"]
            if previous_month
            else None
        ),
        "savings_rate_delta": (
            current_savings_rate - previous_savings_rate
            if current_savings_rate is not None and previous_savings_rate is not None
            else None
        ),
    }

    mom_deltas = category_mom_deltas(transactions, year, month, TransactionType.EXPENSE)
    movers = sorted(
        (
            {
                "name": cat.name,
                "delta": mom_deltas[cat.name].delta,
                "pct_delta": mom_deltas[cat.name].pct_delta,
            }
            for cat in expense_breakdown
            if cat.name in mom_deltas
        ),
        key=lambda m: abs(m["delta"]),
        reverse=True,
    )[:MOVERS_LIMIT]
    movers_txn_links = {
        m["name"]: breadcrumbs.transactions_link(
            category=m["name"],
            txn_type=TransactionType.EXPENSE.value,
            date_from=month_date_from,
            date_to=month_date_to,
            account_id=account_id,
        )
        for m in movers
    }

    is_current_month = (year, month) == (date.today().year, date.today().month)
    expense_forecast = None
    category_forecasts: dict[str, object] = {}
    if is_current_month:
        today = date.today()
        expense_forecast = forecast_month_end(transactions, today)
        for cat in expense_breakdown:
            cat_forecast = forecast_month_end(transactions, today, cat.name)
            if cat_forecast is not None:
                category_forecasts[cat.name] = cat_forecast

    return templates.TemplateResponse(
        request,
        "reports/month.html",
        {
            "rule_months": month_pair(ledger, categories, accounts, year, month),
            "rule_link": f"/reports/budget-rule?month={year:04d}-{month:02d}",
            "year": year,
            "month": month,
            "accounts": accounts,
            "account_id": account_id,
            "label": f"{month_name[month]} {year}",
            "date_from": month_date_from,
            "date_to": month_date_to,
            "month_txn_link": month_txn_link,
            "breadcrumbs": breadcrumbs.for_month(year, month, account_id),
            "expense_treemap_pair": expense_treemap_pair,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "income_config": income_config,
            "expense_config": expense_config,
            "expense_month_rows": _month_breakdown_rows(
                expense_breakdown, expense_config, mom_deltas, category_forecasts
            ),
            "expense_forecast": expense_forecast,
            "show_projected": is_current_month,
            "month_stats": month_stats,
            "movers": movers,
            "movers_txn_links": movers_txn_links,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "prev_label": f"{month_name[prev_month]} {prev_year}",
            "next_year": next_year,
            "next_month": next_month,
            "next_label": f"{month_name[next_month]} {next_year}",
        },
    )


@router.get("/category", response_class=HTMLResponse)
def category_detail(
    request: Request,
    txn_type: TransactionType,
    category: str,
    account_id: str = "",
    subcategories: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Render one category's full, all-time trend, year rollup, and subcategory shares.

    ``category`` is a query param, not a path segment — same convention
    every other category-scoped link in the app already uses
    (``_category_breakdown.html``'s own ``_txn_link``), which also
    sidesteps a category name that happens to contain a literal ``/``
    breaking path routing. ``txn_type`` must be ``INCOME`` or
    ``EXPENSE``; rejected the same way ``/transactions/new/{txn_type}``
    already rejects ``TRANSFER`` — transfers use a fixed category outside
    the managed tree, so there's nothing here to show a trend for.

    Always all-time — the same view ``/reports/categories``' own links
    point at. ``/category/{year}`` and ``/category/{year}/{month}``
    (below) are the scoped counterparts, reached instead from
    ``/reports/{year}``'s and ``/reports/{year}/{month}``'s own
    trend-links.
    """
    return _category_detail_response(
        request, txn_type, category, account_id, None, None, subcategories
    )


@router.get("/category/{year:int}", response_class=HTMLResponse)
def category_detail_year(
    request: Request,
    year: int,
    txn_type: TransactionType,
    category: str,
    account_id: str = "",
    subcategories: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Render one category's stats scoped to one year — see ``category_detail``.

    The trend chart (and, when there is one, the by-subcategory stacked
    chart) trim to just ``year``'s own months instead of the whole
    ledger history; the "This year"/"This month" hero tiles, the avg-
    transaction-size comparison, and the budget streak's 12-month window
    anchor to it too (December, since a fully past year has no "current
    month" of its own — see ``_category_detail_response``). The all-time
    total, by-year table, and highest-month tile are still genuinely
    all-time regardless — a "View all-time stats" link is always one
    click away.
    """
    return _category_detail_response(
        request, txn_type, category, account_id, year, None, subcategories
    )


@router.get("/category/{year:int}/{month:int}", response_class=HTMLResponse)
def category_detail_month(
    request: Request,
    year: int,
    month: int,
    txn_type: TransactionType,
    category: str,
    account_id: str = "",
    subcategories: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Render one category's stats scoped to one month — see ``category_detail``.

    Same trimming as ``category_detail_year`` (the trend/subcategory
    charts still show ``year``'s full 12 months for context — a
    single-month trend chart would just be one bar), with the hero
    tiles anchored to this exact month instead of December.
    """
    if not 1 <= month <= 12:
        raise HTTPException(status_code=404, detail="Invalid month")
    return _category_detail_response(
        request, txn_type, category, account_id, year, month, subcategories
    )


@router.get("/subcategory", response_class=HTMLResponse)
def subcategory_detail(
    request: Request,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
    account_id: str = "",
) -> HTMLResponse:
    """Render one subcategory's full, all-time trend and year rollup.

    A drill-down one level deeper than ``category_detail`` — reached
    from the category page's own "By subcategory" section (clicking a
    subcategory name there, at whichever of This month/This year/All
    time it's currently showing, lands on this same page scoped to that
    period). Shares its entire implementation with ``category_detail``
    via ``_category_detail_response``'s ``subcategory`` keyword — a
    subcategory is a category with one more equality filter applied
    everywhere, not a second parallel set of aggregation/chart code —
    except a subcategory has no further subdivision of its own, so the
    by-subcategory shares/stacked-chart sections are skipped and a
    dedicated ``reports/subcategory.html`` template (no such sections)
    renders instead of ``reports/category.html``.
    """
    return _category_detail_response(
        request, txn_type, category, account_id, None, None, [], subcategory=subcategory
    )


@router.get("/subcategory/{year:int}", response_class=HTMLResponse)
def subcategory_detail_year(
    request: Request,
    year: int,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
    account_id: str = "",
) -> HTMLResponse:
    """Render one subcategory's stats scoped to a year — see ``subcategory_detail``."""
    return _category_detail_response(
        request, txn_type, category, account_id, year, None, [], subcategory=subcategory
    )


@router.get("/subcategory/{year:int}/{month:int}", response_class=HTMLResponse)
def subcategory_detail_month(
    request: Request,
    year: int,
    month: int,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
    account_id: str = "",
) -> HTMLResponse:
    """Render one subcategory's stats scoped to a month — see ``subcategory_detail``."""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=404, detail="Invalid month")
    return _category_detail_response(
        request,
        txn_type,
        category,
        account_id,
        year,
        month,
        [],
        subcategory=subcategory,
    )


def _category_detail_response(
    request: Request,
    txn_type: TransactionType,
    category: str,
    account_id: str,
    year: int | None,
    month: int | None,
    subcategories: list[str],
    *,
    subcategory: str | None = None,
) -> HTMLResponse:
    """Shared implementation behind ``category_detail``/``_year``/``_month``.

    ``year``/``month`` (both optional, ``month`` never set without
    ``year``) scope the hero stats and trim the charts to a specific
    period instead of always "today" — see each route's own docstring
    for exactly what does and doesn't move. ``subcategories`` (repeated
    query param, e.g. ``?subcategories=A&subcategories=B``) filters the
    by-subcategory stacked chart to just those names — a plain
    GET-and-resubmit form, same convention as the account filter on
    this same page, rather than an htmx fragment swap, since a stacked
    chart's y-axis has to rescale to whatever's actually visible and
    that's simplest to get right as one full render. An absent or
    entirely-invalid selection (a stale link after a category's
    subcategories changed, say) falls back to showing every subcategory
    rather than a confusing empty chart.

    ``subcategory`` (keyword-only, distinct from the ``subcategories``
    list above) is set only by ``_subcategory_detail_response`` — it
    narrows every stat/chart down one further level, to one specific
    subcategory within ``category``, and switches the rendered template
    to ``reports/subcategory.html``. A subcategory has no further
    subdivision of its own, so the by-subcategory shares/stacked-chart
    sections (only meaningful for a whole category) are skipped
    entirely in that mode rather than rendered empty.
    """
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid transaction type", status_code=404)

    template_name = (
        "reports/subcategory.html"
        if subcategory is not None
        else "reports/category.html"
    )

    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    category_config = _category_config(categories, txn_type).get(
        category, {"icon": "", "budget": None, "subcategories": {}}
    )
    config = (
        category_config.get("subcategories", {}).get(
            subcategory, {"icon": "", "budget": None}
        )
        if subcategory is not None
        else category_config
    )

    monthly = category_monthly_series(
        transactions, category, txn_type, subcategory=subcategory
    )
    scope_qs = f"txn_type={txn_type.value}&category={quote(category)}"
    if subcategory is not None:
        scope_qs += f"&subcategory={quote(subcategory)}"
    scope_path = (
        "/reports/subcategory" if subcategory is not None else "/reports/category"
    )
    all_time_link = f"{scope_path}?{scope_qs}" + (
        f"&account_id={quote(account_id)}" if account_id else ""
    )
    context = {
        "category": category,
        "subcategory": subcategory,
        "txn_type": txn_type,
        "config": config,
        "accounts": accounts,
        "account_id": account_id,
        "has_data": bool(monthly),
        "breadcrumbs": (
            breadcrumbs.for_subcategory(
                category,
                subcategory,
                account_id,
                txn_type=txn_type.value,
                year=year,
                month=month,
            )
            if subcategory is not None
            else breadcrumbs.for_category(
                category, account_id, txn_type=txn_type.value, year=year, month=month
            )
        ),
        "scope_year": year,
        "scope_month": month,
        "all_time_link": all_time_link,
    }
    if not monthly:
        return templates.TemplateResponse(request, template_name, context)

    today = date.today()
    if year is not None and month is not None:
        ref_year, ref_month = year, month
    elif year is not None:
        # A fully past year has no "current month" of its own — anchor
        # to December so the YTD-style comparison below naturally
        # becomes a plain full-year-vs-full-year one (Jan-Dec vs
        # Jan-Dec), rather than needing a separate code path for it.
        ref_year, ref_month = year, (today.month if year == today.year else 12)
    else:
        ref_year, ref_month = today.year, today.month
    is_current_period = (ref_year, ref_month) == (today.year, today.month)

    all_time_total = sum((p.total for p in monthly), Decimal("0"))
    all_time_count = sum(p.count for p in monthly)

    # Bounded to ref_month, not just ref_year — for the default (today)
    # case this is a no-op, since category_monthly_series never has
    # entries past today anyway, but a fully past ref_year has all 12
    # months in ``monthly`` already, so without this bound a month-
    # scoped view (year+month both given) would silently include months
    # after the one actually being viewed.
    this_year_points = [
        p
        for p in monthly
        if p.key.startswith(f"{ref_year:04d}-")
        and int(p.key.split("-")[1]) <= ref_month
    ]
    this_year_total = sum((p.total for p in this_year_points), Decimal("0"))
    # A same-months-last-year comparison, not "this partial year vs the
    # prior full year" — the by-year table below uses that simpler
    # (if less honest for an in-progress year) plain-total convention
    # instead, same as yearly_totals_with_yoy elsewhere, so the two
    # aren't trying to answer quite the same question. For a fully past
    # ref_year (ref_month forced to 12 above), "same months" is every
    # month, so this degrades into a plain full-year comparison anyway.
    last_year_same_span_total = sum(
        (
            p.total
            for p in monthly
            if p.key.startswith(f"{ref_year - 1:04d}-")
            and int(p.key.split("-")[1]) <= ref_month
        ),
        Decimal("0"),
    )
    ytd_delta = (
        this_year_total - last_year_same_span_total
        if last_year_same_span_total
        else None
    )

    current_key = f"{ref_year:04d}-{ref_month:02d}"
    current_month_point = next((p for p in monthly if p.key == current_key), None)
    current_month_ring = (
        _ring_geometry(current_month_point.total, config["budget"])
        if current_month_point
        else None
    )

    highest_month = (
        max(monthly, key=lambda p: p.total) if any(p.total for p in monthly) else None
    )

    this_year_count = sum(p.count for p in this_year_points)
    last_year_same_span_count = sum(
        p.count
        for p in monthly
        if p.key.startswith(f"{ref_year - 1:04d}-")
        and int(p.key.split("-")[1]) <= ref_month
    )
    this_year_avg_txn = this_year_total / this_year_count if this_year_count else None
    last_year_avg_txn = (
        last_year_same_span_total / last_year_same_span_count
        if last_year_same_span_count
        else None
    )
    avg_txn_delta = (
        this_year_avg_txn - last_year_avg_txn
        if this_year_avg_txn is not None and last_year_avg_txn is not None
        else None
    )

    budget_streak = None
    if config["budget"]:
        # 12 months ending at the scoped period (ref_year/ref_month),
        # not always "ending today" — falls back to the trailing 12 if
        # current_key somehow isn't in monthly (shouldn't happen, since
        # ref_year/ref_month is never later than today and
        # category_monthly_series always extends through today).
        end_index = next(
            (i for i, p in enumerate(monthly) if p.key == current_key), len(monthly) - 1
        )
        window = monthly[max(0, end_index - 11) : end_index + 1]
        dots = [
            "under" if point.total <= config["budget"] else "over" for point in window
        ]
        budget_streak = {
            "dots": dots,
            "under_count": dots.count("under"),
            "total_count": len(dots),
        }

    # A subcategory has no further subdivision of its own, so there's no
    # by-subcategory breakdown to compute once ``subcategory`` is set —
    # every downstream user of ``sub_months``/``sub_series`` degrades
    # gracefully on an empty pair (the stacked chart below simply never
    # gets built, and the subcategory template doesn't reference it).
    sub_months, sub_series = (
        ([], [])
        if subcategory is not None
        else category_subcategory_monthly_series(
            transactions, category, txn_type, today=today
        )
    )

    # Trim the trend chart (and, when there's one, the by-subcategory
    # stacked chart) to just the scoped year's months when reached via
    # /category/{year} or /category/{year}/{month} — a single-month
    # trend chart would be one bar, so month-scoping still shows the
    # whole year for context, same as year-scoping. Falls back to the
    # untrimmed series if the scoped year somehow isn't present
    # (shouldn't happen: ref_year is never later than today, and
    # category_monthly_series always extends through today).
    if year is not None:
        year_prefix = f"{year:04d}-"
        trimmed = [p for p in monthly if p.key.startswith(year_prefix)]
        chart_monthly = trimmed or monthly
        sub_indices = [
            i for i, m in enumerate(sub_months) if m.key.startswith(year_prefix)
        ]
        if sub_indices:
            start, end = sub_indices[0], sub_indices[-1] + 1
            chart_sub_months = sub_months[start:end]
            chart_sub_series = [
                SubcategoryMonthPoint(name=s.name, totals=s.totals[start:end])
                for s in sub_series
            ]
        else:
            chart_sub_months, chart_sub_series = sub_months, sub_series
    else:
        chart_monthly = monthly
        chart_sub_months, chart_sub_series = sub_months, sub_series

    # A dedicated "this year at a glance" tile set, shown only when
    # scoped to a year *and no more specific month* (/category/{year}
    # only) — once a month is also given, month_stats below is the
    # page's own tile set instead; showing both at once just duplicated
    # "the year" and "the month" side by side on what's meant to be a
    # single-month page.
    year_stats = None
    if year is not None and month is None:
        year_total = sum((p.total for p in chart_monthly), Decimal("0"))
        year_count = sum(p.count for p in chart_monthly)
        year_month_totals = [p.total for p in chart_monthly]
        # A no-activity month is excluded from "lowest" (same reasoning
        # as _month_cells' own extreme-marking: an absence of data isn't
        # a genuinely low month) but not from "highest" — a magnitude
        # can only be the max while also being zero if literally every
        # month is, which "any(...)" below already guards against.
        year_nonzero_months = [p for p in chart_monthly if p.total]
        year_stats = {
            "total": year_total,
            "count": year_count,
            "avg_per_month": (
                year_total / len(chart_monthly) if chart_monthly else None
            ),
            "median": (
                statistics.median(year_month_totals) if year_month_totals else None
            ),
            "highest_month": (
                max(chart_monthly, key=lambda p: p.total)
                if any(p.total for p in chart_monthly)
                else None
            ),
            "lowest_month": (
                min(year_nonzero_months, key=lambda p: p.total)
                if year_nonzero_months
                else None
            ),
            "avg_txn": year_total / year_count if year_count else None,
            "budget_streak": (
                {
                    "dots": [
                        "under" if p.total <= config["budget"] else "over"
                        for p in chart_monthly
                    ],
                    "under_count": sum(
                        1 for p in chart_monthly if p.total <= config["budget"]
                    ),
                    "total_count": len(chart_monthly),
                }
                if config["budget"]
                else None
            ),
        }

        # "By month" table rows — each month's own total/count plus a
        # vs-prior-month delta, looked up against the *full* (untrimmed)
        # ``monthly`` series rather than ``chart_monthly`` alone so
        # January's delta still compares against the prior December even
        # though that December falls outside this calendar year.
        month_index_by_key = {p.key: i for i, p in enumerate(monthly)}
        month_rows = []
        for point in chart_monthly:
            index = month_index_by_key[point.key]
            previous_total = monthly[index - 1].total if index > 0 else None
            month_rows.append(
                {
                    "month": int(point.key.split("-")[1]),
                    "label": point.label,
                    "total": point.total,
                    "count": point.count,
                    "delta": (
                        point.total - previous_total
                        if previous_total is not None
                        else None
                    ),
                }
            )
        year_stats["month_rows"] = month_rows

    # A dedicated "this month" tile set — individual-transaction avg/
    # median/min/max and a pass/fail budget check, not monthly totals
    # the way year_stats works, since a single month has no "months" of
    # its own to summarize across. Also swaps the trend chart for a
    # weekly-bucketed one (a single month has no monthly trend to show)
    # and narrows the by-subcategory shares to just this month.
    month_stats = None
    week_chart = None
    week_subcategory_chart = None
    month_shares = None
    if month is not None:
        month_txns = [
            t
            for t in transactions
            if t.type is txn_type
            and t.category == category
            and (subcategory is None or t.subcategory == subcategory)
            and t.date.year == year
            and t.date.month == month
        ]
        month_amounts = [abs(t.amount) for t in month_txns]
        month_total = sum(month_amounts, Decimal("0"))
        month_count = len(month_txns)
        top_month_txns = sorted(month_txns, key=lambda t: abs(t.amount), reverse=True)[
            :5
        ]
        month_stats = {
            "total": month_total,
            "count": month_count,
            "avg_txn": month_total / month_count if month_count else None,
            "median_txn": (statistics.median(month_amounts) if month_amounts else None),
            "min_txn": min(month_amounts) if month_amounts else None,
            "max_txn": max(month_amounts) if month_amounts else None,
            "budget_ring": _ring_geometry(month_total, config["budget"]),
            "top_transactions": [
                {
                    "txn": t,
                    "account_name": next(
                        (a.name for a in accounts if a.id == t.account_id),
                        t.account_id,
                    ),
                }
                for t in top_month_txns
            ],
        }

        week_points, week_ranges = _weekly_points_for_month(
            transactions, category, txn_type, year, month, subcategory=subcategory
        )
        week_chart = _svg_category_week_chart(
            week_points,
            week_ranges,
            txn_type=txn_type,
            category=category,
            subcategory=subcategory or "",
            account_id=account_id,
        )

        # A subcategory has no further subdivision, so there's no shares
        # breakdown or stacked chart to build once ``subcategory`` is set.
        if subcategory is None:
            month_shares = category_subcategory_shares(
                transactions, category, txn_type, year=year, month=month
            )

            week_sub_points, week_sub_series, week_sub_ranges = (
                _weekly_subcategory_points_for_month(
                    transactions, category, txn_type, year, month
                )
            )
            if week_sub_series:
                week_subcategory_chart = _svg_subcategory_week_stack_chart(
                    week_sub_points,
                    week_sub_series,
                    week_sub_ranges,
                    txn_type=txn_type,
                    category=category,
                    account_id=account_id,
                )

    # A subcategory with zero activity in the shown (possibly trimmed)
    # window shouldn't get a legend checkbox for a series that's always
    # flat at zero.
    chart_sub_series = [s for s in chart_sub_series if any(s.totals)]

    available_subcategories = [s.name for s in chart_sub_series]
    selected_subcategories = [
        name for name in subcategories if name in available_subcategories
    ]
    visible_subcategories = set(selected_subcategories or available_subcategories)
    subcategory_chart = (
        week_subcategory_chart
        if week_subcategory_chart is not None
        else (
            _svg_subcategory_stack_chart(
                chart_sub_months,
                chart_sub_series,
                visible=visible_subcategories,
                txn_type=txn_type,
                category=category,
                account_id=account_id,
            )
            if chart_sub_series
            else None
        )
    )
    # True once the user has actually narrowed the selection (as opposed
    # to nothing being selected yet, which falls back to "everything") —
    # the template uses this to decide whether "Show all" is worth
    # showing at all.
    subcategory_filter_active = bool(
        selected_subcategories
        and len(selected_subcategories) < len(available_subcategories)
    )

    context.update(
        {
            "chart": (
                week_chart
                if week_chart is not None
                else _svg_category_chart(
                    chart_monthly,
                    budget=config["budget"],
                    txn_type=txn_type,
                    category=category,
                    subcategory=subcategory or "",
                    account_id=account_id,
                )
            ),
            "chart_is_weekly": week_chart is not None,
            "chart_first_month_label": chart_monthly[0].label,
            "chart_last_month_label": chart_monthly[-1].label,
            "month_stats": month_stats,
            "month_shares": month_shares,
            "month_date_bounds": (
                _month_date_bounds(f"{year:04d}-{month:02d}")
                if month is not None
                else None
            ),
            "subcategory_chart": subcategory_chart,
            "subcategory_chart_is_weekly": week_subcategory_chart is not None,
            "subcategory_filter_active": subcategory_filter_active,
            "monthly": list(reversed(chart_monthly)),
            "all_time_total": all_time_total,
            "all_time_count": all_time_count,
            "avg_per_month": all_time_total / len(monthly),
            "months_covered": len(monthly),
            "first_month_label": monthly[0].label,
            "last_month_label": monthly[-1].label,
            "this_year": today.year,
            "current_month": today.month,
            "this_year_total": this_year_total,
            "ytd_delta": ytd_delta,
            "current_month_label": f"{month_abbr[ref_month]} {ref_year}",
            "year_tile_label": (
                f"{ref_year} YTD" if ref_year == today.year else str(ref_year)
            ),
            "month_tile_heading": (
                f"This month · {month_abbr[ref_month]} {ref_year}"
                if is_current_period
                else f"{month_abbr[ref_month]} {ref_year}"
            ),
            "current_month_point": current_month_point,
            "current_month_ring": current_month_ring,
            "highest_month": highest_month,
            "years": category_yearly_series(
                transactions, category, txn_type, subcategory=subcategory
            ),
            "all_time_shares": (
                category_subcategory_shares(transactions, category, txn_type)
                if subcategory is None
                else None
            ),
            "this_year_shares": (
                category_subcategory_shares(
                    transactions, category, txn_type, year=today.year
                )
                if subcategory is None
                else None
            ),
            "avg_transaction_size": (
                all_time_total / all_time_count if all_time_count else None
            ),
            "avg_txn_delta": avg_txn_delta,
            "this_year_txns_per_month": (
                this_year_count / len(this_year_points) if this_year_points else 0
            ),
            "last_year_txns_per_month": (
                last_year_same_span_count / len(this_year_points)
                if this_year_points
                else 0
            ),
            "budget_streak": budget_streak,
            "year_stats": year_stats,
        }
    )
    return templates.TemplateResponse(request, template_name, context)
