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
from calendar import month_abbr, month_name
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models.category import CategoriesByType
from app.models.transaction import TransactionType
from app.services.aggregation import (
    CategoryTotal,
    category_breakdown,
    category_monthly_totals,
    category_totals_for_month,
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
    }


def _month_cells(
    month_amounts: dict[str, Decimal], budget: Decimal | None, month_keys: list[str]
) -> list[dict]:
    """Build one row's per-month cells: an amount plus ring geometry, if budgeted."""
    cells = []
    for key in month_keys:
        amount = month_amounts.get(key, Decimal("0"))
        cells.append({"amount": amount, "ring": _ring_geometry(amount, budget)})
    return cells


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
    separate one per month.
    """
    rows = []
    for category in breakdown:
        cat_config = config.get(category.name, {})
        rows.append(
            {
                "name": category.name,
                "icon": cat_config.get("icon", ""),
                "indent": False,
                "monthly": _month_cells(
                    monthly_totals.get(category.name, {}),
                    cat_config.get("budget"),
                    month_keys,
                ),
                "total": category.total,
            }
        )
        sub_monthly = subcategory_monthly.get(category.name, {})
        sub_configs = cat_config.get("subcategories", {})
        for sub in category.subcategories:
            sub_config = sub_configs.get(sub.name, {})
            rows.append(
                {
                    "name": sub.name,
                    "icon": sub_config.get("icon", ""),
                    "indent": True,
                    "monthly": _month_cells(
                        sub_monthly.get(sub.name, {}),
                        sub_config.get("budget"),
                        month_keys,
                    ),
                    "total": sub.total,
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


def _svg_net_worth_chart(
    rows: list[tuple[str, Decimal, Decimal, Decimal]],
    *,
    width: int = 720,
    height: int = 260,
    pad_left: int = 72,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 16,
    tick_step: float | None = None,
) -> dict:
    """Return template-ready SVG geometry: a net worth line plus income/expense bars.

    ``rows`` is ``(label, net_worth, income_total, expense_total)``
    quadruples, oldest first, all for the same set of months. Returns
    ``{"has_data": False}`` for no rows. Income/expense plot as
    magnitudes (``abs``) rising from the shared zero baseline — mixing a
    signed expense total with an unsigned bar height would read wrong —
    while the net worth line plots its actual (signed) value against
    the same baseline. Y-axis ticks start from 0 (see ``_tick_bounds``)
    at a step ``_nice_step`` picks to fit the data (``tick_step``
    overrides that, mainly for tests that want an exact, predictable
    step rather than whatever the sample data happens to produce).
    """
    if not rows:
        return {"has_data": False}

    net_worth_values = [float(net_worth) for _, net_worth, _, _ in rows]
    magnitudes = [abs(float(income)) for _, _, income, _ in rows] + [
        abs(float(expense)) for _, _, _, expense in rows
    ]
    all_values = net_worth_values + magnitudes
    step = (
        tick_step
        if tick_step is not None
        else _nice_step(max((abs(v) for v in all_values), default=0))
    )
    y_min, y_max = _tick_bounds(all_values, step)

    plot_left = pad_left
    plot_right = width - pad_right
    plot_width = plot_right - plot_left
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top
    count = len(rows)
    group_width = plot_width / count
    bar_width = group_width * 0.32
    gap = group_width * 0.06

    def x_at(index: int) -> float:
        if count == 1:
            return plot_left + plot_width / 2
        return plot_left + plot_width * index / (count - 1)

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    zero_y = y_at(0)

    bars = []
    coords = []
    for index, (label, net_worth, income, expense) in enumerate(rows):
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
        coords.append((x_at(index), y_at(float(net_worth))))

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
        "labels": [label for label, _, _, _ in rows],
        "net_worth_labels": [f"{float(net_worth):,.2f}" for _, net_worth, _, _ in rows],
        "y_ticks": y_ticks,
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

        if len(top) == 1:
            mid = angle + 180
            x1, y1 = point(angle)
            xm, ym = point(mid)
            x2, y2 = point(end_angle)
            path_d = (
                f"M {cx:.2f},{cy:.2f} L {x1:.2f},{y1:.2f} "
                f"A {radius:.2f},{radius:.2f} 0 1 1 {xm:.2f},{ym:.2f} "
                f"A {radius:.2f},{radius:.2f} 0 1 1 {x2:.2f},{y2:.2f} Z"
            )
        else:
            x1, y1 = point(angle)
            x2, y2 = point(end_angle)
            large_arc = 1 if (end_angle - angle) > 180 else 0
            path_d = (
                f"M {cx:.2f},{cy:.2f} L {x1:.2f},{y1:.2f} "
                f"A {radius:.2f},{radius:.2f} 0 {large_arc} 1 {x2:.2f},{y2:.2f} Z"
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


@router.get("", response_class=HTMLResponse)
def reports_overview(request: Request, account_id: str = "") -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary."""
    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    net_worth_accounts = (
        [a for a in accounts if a.id == account_id] if account_id else accounts
    )
    net_worth_points = net_worth_by_month(transactions, net_worth_accounts)
    monthly_totals = {
        month.key: month for month in monthly_totals_with_mom(transactions)
    }
    chart = _svg_net_worth_chart(
        [
            (
                point.label,
                point.value,
                monthly_totals[point.key].income_total,
                monthly_totals[point.key].expense_total,
            )
            for point in net_worth_points
        ]
    )
    years = yearly_totals_with_yoy(transactions)
    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {
            "years": years,
            "chart": chart,
            "accounts": accounts,
            "account_id": account_id,
        },
    )


@router.get("/{year}", response_class=HTMLResponse)
def year_detail(request: Request, year: int, account_id: str = "") -> HTMLResponse:
    """Render one year's monthly breakdown and income/expense category drill-down."""
    accounts = read_accounts()
    transactions = _filter_by_account(read_ledger(), account_id)
    categories = read_categories()
    income_breakdown = category_breakdown(transactions, year, TransactionType.INCOME)
    expense_breakdown = category_breakdown(transactions, year, TransactionType.EXPENSE)
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
    return templates.TemplateResponse(
        request,
        "reports/year.html",
        {
            "year": year,
            "accounts": accounts,
            "account_id": account_id,
            "months": months,
            "month_labels": month_labels,
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


@router.get("/{year}/{month}", response_class=HTMLResponse)
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
    return templates.TemplateResponse(
        request,
        "reports/month.html",
        {
            "year": year,
            "month": month,
            "accounts": accounts,
            "account_id": account_id,
            "label": f"{month_name[month]} {year}",
            "spending_pie": spending_pie,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "income_config": income_config,
            "expense_config": expense_config,
        },
    )
