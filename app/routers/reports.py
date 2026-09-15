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
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.models.category import CategoriesByType
from app.models.transaction import TransactionType
from app.services.aggregation import (
    category_breakdown,
    monthly_totals_with_mom,
    net_worth_by_month,
    yearly_totals_with_yoy,
)
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports", tags=["reports"])


def _category_icons(categories: CategoriesByType, txn_type: TransactionType) -> dict:
    """Return ``{category_name: {"icon": ..., "subcategories": {sub_name: icon}}}``.

    ``category_breakdown`` is a pure ledger aggregation with no
    knowledge of ``config/categories.toml`` (icons are config, not
    ledger data), so the breakdown table's icons are looked up here and
    passed to the template separately, rather than teaching
    ``services.aggregation`` about category config.
    """
    tree = categories.get(txn_type.value, {})
    return {
        name: {
            "icon": entry["icon"],
            "subcategories": {
                sub_name: sub_entry["icon"]
                for sub_name, sub_entry in entry["subcategories"].items()
            },
        }
        for name, entry in tree.items()
    }


def _tick_bounds(values: list[float], step: float) -> tuple[float, float]:
    """Round ``values``' range outward to a multiple of ``step``, always spanning 0.

    Anchoring every tick to a multiple of ``step`` starting from 0
    (rather than the data's own min/max) is what the user asked for
    directly: "0 and then every 5000" — round numbers you can read off
    at a glance, not values that happen to line up with the data.
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
    tick_step: int = 5000,
) -> dict:
    """Return template-ready SVG geometry: a net worth line plus income/expense bars.

    ``rows`` is ``(label, net_worth, income_total, expense_total)``
    quadruples, oldest first, all for the same set of months. Returns
    ``{"has_data": False}`` for no rows. Income/expense plot as
    magnitudes (``abs``) rising from the shared zero baseline — mixing a
    signed expense total with an unsigned bar height would read wrong —
    while the net worth line plots its actual (signed) value against
    the same baseline. Y-axis ticks are fixed at every ``tick_step``
    starting from 0 (see ``_tick_bounds``), covering whichever of the
    line or the bars reaches further.
    """
    if not rows:
        return {"has_data": False}

    net_worth_values = [float(net_worth) for _, net_worth, _, _ in rows]
    magnitudes = [abs(float(income)) for _, _, income, _ in rows] + [
        abs(float(expense)) for _, _, _, expense in rows
    ]
    y_min, y_max = _tick_bounds(net_worth_values + magnitudes, tick_step)

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

    step_count = round((y_max - y_min) / tick_step)
    y_ticks = [
        {
            "y": y_at(y_min + i * tick_step),
            "label": f"{y_min + i * tick_step:,.0f}",
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


@router.get("", response_class=HTMLResponse)
def reports_overview(request: Request) -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary."""
    transactions = read_ledger()
    accounts = read_accounts()
    net_worth_points = net_worth_by_month(transactions, accounts)
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
        request, "reports/list.html", {"years": years, "chart": chart}
    )


@router.get("/{year}", response_class=HTMLResponse)
def year_detail(request: Request, year: int) -> HTMLResponse:
    """Render one year's monthly breakdown and income/expense category drill-down."""
    transactions = read_ledger()
    categories = read_categories()
    income_breakdown = category_breakdown(transactions, year, TransactionType.INCOME)
    expense_breakdown = category_breakdown(transactions, year, TransactionType.EXPENSE)
    months = [
        month
        for month in monthly_totals_with_mom(transactions)
        if month.key.startswith(f"{year:04d}-")
    ]
    return templates.TemplateResponse(
        request,
        "reports/year.html",
        {
            "year": year,
            "months": months,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "income_icons": _category_icons(categories, TransactionType.INCOME),
            "expense_icons": _category_icons(categories, TransactionType.EXPENSE),
        },
    )
