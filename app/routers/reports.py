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
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.models.category import CategoriesByType
from app.models.transaction import TransactionType
from app.routers import breadcrumbs
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
    category_totals_for_month,
    category_yearly_series,
    monthly_totals_with_mom,
    net_worth_by_month,
    subcategory_monthly_totals,
    yearly_totals_with_yoy,
)
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports", tags=["reports"])

# Shared by the landing page's and the month drill-down's own "Biggest
# movers" lists — how many ranked categories each shows.
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
) -> list[dict]:
    """Build expense-by-category rows for the month drill-down's budget/MoM table.

    Precomputes each row's ring geometry (``_ring_geometry``) and MoM
    delta here rather than in the template — same "geometry is a router
    concern, not a Jinja concern" split as ``_category_month_matrix``.
    ``deltas`` is ``category_mom_deltas``'s result; a category/subcategory
    missing from it (no activity in either month, or type mismatch) gets
    ``delta=None`` rather than a KeyError.
    """
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
    used only to build the x-axis month/year labels below. Returns
    ``{"has_data": False}`` for no rows. Income/expense plot as
    magnitudes (``abs``) rising from the shared zero baseline — mixing a
    signed expense total with an unsigned bar height would read wrong —
    while the net worth line plots its actual (signed) value against
    the same baseline. Y-axis ticks start from 0 (see ``_tick_bounds``)
    at a step ``_nice_step`` picks to fit the data (``tick_step``
    overrides that, mainly for tests that want an exact, predictable
    step rather than whatever the sample data happens to produce).

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
        bars.append(
            {
                "income_x": center - gap / 2 - bar_width,
                "income_y": income_y,
                "income_h": zero_y - income_y,
                "income_label": f"{abs(income):,.2f}",
                "expense_x": center + gap / 2,
                "expense_y": expense_y,
                "expense_h": zero_y - expense_y,
                "expense_label": f"{abs(expense):,.2f}",
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
    color meaning.
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
        bars.append(
            {
                "x": center - bar_width / 2,
                "bar_width": bar_width,
                "segments": segments,
                "label": point.label,
                "total_label": f"{point.total:,.2f}",
                "count": point.count,
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


# Fixed-order categorical colors for the spending pie chart's top slices —
# same "assign identity by fixed order, never cycle" convention as any
# other categorical series in the app. "Other" (the collapsed tail past
# the top-N slices) always gets its own muted gray instead of the next
# hue in line, since it isn't one category's identity. Every slice is
# also named in the accompanying legend and via an SVG <title> tooltip,
# so identity never depends on telling two similar hues apart by eye
# alone.
_PIE_SLICE_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
    "#0d366b",  # deep blue
    "#7a4b1e",  # brown
]


def _pie_slice_path(
    cx: float,
    cy: float,
    radius: float,
    point: Callable[[float], tuple[float, float]],
    angle: float,
    end_angle: float,
    *,
    full_circle: bool,
) -> str:
    """Build one pie wedge's SVG path ``d=`` attribute, or a full circle.

    A lone 100%-share slice (``full_circle=True``) needs two joined
    semicircle arcs instead of the normal move-to-center/arc/close wedge
    shape, since a single SVG arc command can't describe a full circle
    (its start and end point would coincide).
    """
    if full_circle:
        mid = angle + 180
        x1, y1 = point(angle)
        xm, ym = point(mid)
        x2, y2 = point(end_angle)
        return (
            f"M {cx:.2f},{cy:.2f} L {x1:.2f},{y1:.2f} "
            f"A {radius:.2f},{radius:.2f} 0 1 1 {xm:.2f},{ym:.2f} "
            f"A {radius:.2f},{radius:.2f} 0 1 1 {x2:.2f},{y2:.2f} Z"
        )
    x1, y1 = point(angle)
    x2, y2 = point(end_angle)
    large_arc = 1 if (end_angle - angle) > 180 else 0
    return (
        f"M {cx:.2f},{cy:.2f} L {x1:.2f},{y1:.2f} "
        f"A {radius:.2f},{radius:.2f} 0 {large_arc} 1 {x2:.2f},{y2:.2f} Z"
    )


def _svg_pie_chart(
    items: list[tuple[str, Decimal]], *, limit: int = 10, size: int = 220
) -> dict:
    """Return template-ready SVG geometry for a top-``limit``-plus-"Other" pie chart.

    ``items`` is ``(category_name, amount)`` pairs, already sorted by
    amount descending (as ``category_totals_for_month`` returns them) —
    magnitudes, not signed, same convention as the rest of the category
    breakdown views. Categories past ``limit`` are collapsed into one
    "Other" slice rather than growing the palette indefinitely (a 15th
    distinct hue stops being reliably distinguishable at a glance either
    way — see ``_PIE_SLICE_COLORS``). Returns ``{"has_data": False}`` for
    no data or a zero total. A lone 100% slice is drawn as two joined
    semicircle arcs, since a single SVG arc command can't describe a
    full circle (its start and end point would coincide).
    """
    items = [(name, amount) for name, amount in items if amount > 0]
    if not items:
        return {"has_data": False}

    top = items[:limit]
    rest = items[limit:]
    if rest:
        top.append(("Other", sum((amount for _, amount in rest), Decimal("0"))))

    total = sum((amount for _, amount in top), Decimal("0"))
    if total <= 0:
        return {"has_data": False}

    cx = cy = size / 2
    radius = size / 2 - 4

    def point(angle_deg: float) -> tuple[float, float]:
        angle = math.radians(angle_deg)
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    slices = []
    angle = -90.0  # 12 o'clock, sweeping clockwise
    for index, (name, amount) in enumerate(top):
        fraction = float(amount / total)
        end_angle = angle + fraction * 360
        is_other = rest and name == "Other"
        css_class = (
            "pie-slice-other"
            if is_other
            else f"pie-slice-{index % len(_PIE_SLICE_COLORS)}"
        )

        path_d = _pie_slice_path(
            cx, cy, radius, point, angle, end_angle, full_circle=len(top) == 1
        )

        pct = fraction * 100
        label_x, label_y = point((angle + end_angle) / 2) if fraction < 1 else (cx, cy)
        # Blend label point 65% of the way from center to the slice's own
        # arc point, so the percentage sits inside the wedge, not on its edge.
        label_x = cx + (label_x - cx) * 0.65
        label_y = cy + (label_y - cy) * 0.65

        slices.append(
            {
                "path_d": path_d,
                "css_class": css_class,
                "label": name,
                "amount": amount,
                "pct_label": f"{pct:.0f}%",
                "show_label": pct >= 6,
                "label_x": label_x,
                "label_y": label_y,
            }
        )
        angle = end_angle

    return {"has_data": True, "size": size, "slices": slices, "total": total}


def _svg_subcategory_stack_chart(
    months: list[CategoryMonthPoint],
    series: list[SubcategoryMonthPoint],
    *,
    visible: set[str] | None = None,
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
    visible ``series`` entry instead of a single fill — colored via the
    ``pie-slice-N``/``pie-slice-other`` CSS classes ``_svg_pie_chart``
    already uses (fixed categorical order, "Other" always a muted gray),
    so a subcategory's color means the same thing whether it's looked up
    on the month drill-down's pie chart or here. No budget line: unlike
    ``_svg_category_chart``, there's no single figure here to compare a
    *stack* against — a category's own budget is checked against its
    combined total, not any one subcategory's slice of it.

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
    activity at all — already skips calling this).
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
        ]
    )


def _ytd_income_expense(
    transactions: list, year: int, cutoff_month: int
) -> tuple[Decimal, Decimal]:
    """Return ``(income_total, expense_total)`` for Jan 1 through ``cutoff_month``.

    Signed, same convention as ``YearlyTotal`` (income positive, expense
    negative). Backs the landing page's YTD-vs-same-months-last-year hero
    stats — same "compare like-for-like calendar span" convention as
    ``category_detail``'s own YTD tile, generalized to the whole ledger
    instead of one category.
    """
    income_total = Decimal("0")
    expense_total = Decimal("0")
    for transaction in transactions:
        if transaction.date.year != year or transaction.date.month > cutoff_month:
            continue
        if transaction.type is TransactionType.INCOME:
            income_total += transaction.amount
        elif transaction.type is TransactionType.EXPENSE:
            expense_total += transaction.amount
    return income_total, expense_total


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


@router.get("", response_class=HTMLResponse)
def reports_overview(request: Request, account_id: str = "") -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary."""
    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
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
    movers = [
        m
        for m in category_movers(transactions, TransactionType.EXPENSE, today)
        if m.delta is not None
    ][:MOVERS_LIMIT]
    movers_txn_links = {
        m.name: breadcrumbs.transactions_link(
            category=m.name,
            txn_type=TransactionType.EXPENSE.value,
            account_id=account_id,
        )
        for m in movers
    }

    this_income, this_expense = _ytd_income_expense(
        transactions, today.year, today.month
    )
    last_income, last_expense = _ytd_income_expense(
        transactions, today.year - 1, today.month
    )
    this_net = this_income + this_expense
    last_net = last_income + last_expense
    this_savings_rate = _savings_rate(this_net, this_income)
    last_savings_rate = _savings_rate(last_net, last_income)
    ytd_stats = {
        "income_total": this_income,
        "expense_total": this_expense,
        "net_total": this_net,
        "savings_rate": this_savings_rate,
        "income_delta": this_income - last_income if last_income else None,
        "expense_delta": this_expense - last_expense if last_expense else None,
        "net_delta": this_net - last_net if last_income or last_expense else None,
        "savings_rate_delta": (
            this_savings_rate - last_savings_rate
            if this_savings_rate is not None and last_savings_rate is not None
            else None
        ),
    }

    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {
            "years": years,
            "chart": chart,
            "accounts": accounts,
            "account_id": account_id,
            "year_txn_links": year_txn_links,
            "year_stats": _year_stats(years, today),
            "movers": movers,
            "movers_txn_links": movers_txn_links,
            "expense_config": expense_config,
            "ytd_stats": ytd_stats,
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
    """Render one month's spending pie chart and income/expense category breakdown."""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=404, detail="Invalid month")

    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    expense_breakdown = category_totals_for_month(
        transactions, year, month, TransactionType.EXPENSE
    )
    income_breakdown = category_totals_for_month(
        transactions, year, month, TransactionType.INCOME
    )
    spending_pie = _svg_pie_chart([(cat.name, cat.total) for cat in expense_breakdown])
    income_config = _category_config(categories, TransactionType.INCOME)
    expense_config = _category_config(categories, TransactionType.EXPENSE)
    prev_year, prev_month = _adjacent_month(year, month, -1)
    next_year, next_month = _adjacent_month(year, month, 1)
    month_date_from, month_date_to = _month_date_bounds(f"{year:04d}-{month:02d}")
    month_txn_link = breadcrumbs.transactions_link(
        date_from=month_date_from, date_to=month_date_to, account_id=account_id
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

    return templates.TemplateResponse(
        request,
        "reports/month.html",
        {
            "year": year,
            "month": month,
            "accounts": accounts,
            "account_id": account_id,
            "label": f"{month_name[month]} {year}",
            "date_from": month_date_from,
            "date_to": month_date_to,
            "month_txn_link": month_txn_link,
            "breadcrumbs": breadcrumbs.for_month(year, month, account_id),
            "spending_pie": spending_pie,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "income_config": income_config,
            "expense_config": expense_config,
            "expense_month_rows": _month_breakdown_rows(
                expense_breakdown, expense_config, mom_deltas
            ),
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
    """Render one category's full-history trend, year rollup, and subcategory shares.

    ``category`` is a query param, not a path segment — same convention
    every other category-scoped link in the app already uses
    (``_category_breakdown.html``'s own ``_txn_link``), which also
    sidesteps a category name that happens to contain a literal ``/``
    breaking path routing. ``txn_type`` must be ``INCOME`` or
    ``EXPENSE``; rejected the same way ``/transactions/new/{txn_type}``
    already rejects ``TRANSFER`` — transfers use a fixed category outside
    the managed tree, so there's nothing here to show a trend for.

    ``subcategories`` (repeated query param, e.g. ``?subcategories=A&
    subcategories=B``) filters the by-subcategory stacked chart to just
    those names — a plain GET-and-resubmit form, same convention as the
    account filter on this same page, rather than an htmx fragment swap,
    since a stacked chart's y-axis has to rescale to whatever's actually
    visible and that's simplest to get right as one full render. An
    absent or entirely-invalid selection (a stale link after a
    category's subcategories changed, say) falls back to showing every
    subcategory rather than a confusing empty chart.
    """
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid transaction type", status_code=404)

    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    config = _category_config(categories, txn_type).get(
        category, {"icon": "", "budget": None}
    )

    monthly = category_monthly_series(transactions, category, txn_type)
    context = {
        "category": category,
        "txn_type": txn_type,
        "config": config,
        "accounts": accounts,
        "account_id": account_id,
        "has_data": bool(monthly),
        "breadcrumbs": breadcrumbs.for_category(
            category, account_id, txn_type=txn_type.value
        ),
    }
    if not monthly:
        return templates.TemplateResponse(request, "reports/category.html", context)

    today = date.today()
    all_time_total = sum((p.total for p in monthly), Decimal("0"))
    all_time_count = sum(p.count for p in monthly)

    this_year_points = [p for p in monthly if p.key.startswith(f"{today.year:04d}-")]
    this_year_total = sum((p.total for p in this_year_points), Decimal("0"))
    # A same-months-last-year comparison, not "this partial year vs the
    # prior full year" — the by-year table below uses that simpler
    # (if less honest for an in-progress year) plain-total convention
    # instead, same as yearly_totals_with_yoy elsewhere, so the two
    # aren't trying to answer quite the same question.
    last_year_same_span_total = sum(
        (
            p.total
            for p in monthly
            if p.key.startswith(f"{today.year - 1:04d}-")
            and int(p.key.split("-")[1]) <= today.month
        ),
        Decimal("0"),
    )
    ytd_delta = (
        this_year_total - last_year_same_span_total
        if last_year_same_span_total
        else None
    )

    current_key = f"{today.year:04d}-{today.month:02d}"
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
        if p.key.startswith(f"{today.year - 1:04d}-")
        and int(p.key.split("-")[1]) <= today.month
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
        last_12 = monthly[-12:]
        dots = [
            "under" if point.total <= config["budget"] else "over" for point in last_12
        ]
        budget_streak = {
            "dots": dots,
            "under_count": dots.count("under"),
            "total_count": len(dots),
        }

    sub_months, sub_series = category_subcategory_monthly_series(
        transactions, category, txn_type, today=today
    )
    available_subcategories = [s.name for s in sub_series]
    selected_subcategories = [
        name for name in subcategories if name in available_subcategories
    ]
    visible_subcategories = set(selected_subcategories or available_subcategories)
    subcategory_chart = (
        _svg_subcategory_stack_chart(
            sub_months, sub_series, visible=visible_subcategories
        )
        if sub_series
        else None
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
            "chart": _svg_category_chart(monthly, budget=config["budget"]),
            "subcategory_chart": subcategory_chart,
            "subcategory_filter_active": subcategory_filter_active,
            "monthly": list(reversed(monthly)),
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
            "current_month_label": f"{month_abbr[today.month]} {today.year}",
            "current_month_point": current_month_point,
            "current_month_ring": current_month_ring,
            "highest_month": highest_month,
            "years": category_yearly_series(transactions, category, txn_type),
            "all_time_shares": category_subcategory_shares(
                transactions, category, txn_type
            ),
            "this_year_shares": category_subcategory_shares(
                transactions, category, txn_type, year=today.year
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
        }
    )
    return templates.TemplateResponse(request, "reports/category.html", context)
