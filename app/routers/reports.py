"""Routes for the reporting & visualization views.

Read-only — no forms, no writes. Every view pulls from the shared
``services.aggregation`` group-by layer (CLAUDE.md's "one shared
aggregation layer" invariant) rather than one-off per-view logic.

Both charts (net worth over time as a line, annual income/expense as
paired bars) are rendered as plain inline SVG computed here, not via a
vendored JS charting library — a deliberate choice (see CLAUDE.md) to
keep the app's zero-network-dependency, minimal-JS posture intact
rather than trusting/maintaining a third-party JS file. ``_svg_line_
chart``/``_svg_bar_chart`` are presentational geometry, not a business
rule, so they live here rather than in ``services`` — a future swap to
a JS charting library would only touch these functions and the
template, not ``services.aggregation``'s data.
"""

from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.models.transaction import TransactionType
from app.services.aggregation import (
    category_breakdown,
    monthly_totals_with_mom,
    net_worth_by_month,
    yearly_totals_with_yoy,
)
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports", tags=["reports"])


def _svg_line_chart(
    points: list[tuple[str, Decimal]],
    *,
    width: int = 720,
    height: int = 220,
    pad_left: int = 64,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 16,
) -> dict:
    """Return template-ready SVG geometry for a line chart of ``points``.

    ``points`` is ``(label, value)`` pairs, oldest first. Returns
    ``{"has_data": False}`` for no points. A flat series (every value
    equal) gets an artificial +/-1 padding on the value range so the
    line doesn't divide by zero. ``pad_left`` is wider than the other
    three sides to leave room for the y-axis value labels.
    """
    if not points:
        return {"has_data": False}

    values = [float(value) for _, value in points]
    y_min, y_max = min(values), max(values)
    if y_min == y_max:
        y_min -= 1
        y_max += 1

    plot_left = pad_left
    plot_right = width - pad_right
    plot_width = plot_right - plot_left
    plot_height = height - pad_top - pad_bottom
    count = len(points)

    def x_at(index: int) -> float:
        if count == 1:
            return plot_left + plot_width / 2
        return plot_left + plot_width * index / (count - 1)

    def y_at(value: float) -> float:
        return pad_top + plot_height * (1 - (value - y_min) / (y_max - y_min))

    coords = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    zero_y = y_at(0) if y_min < 0 < y_max else None

    # Three evenly-spaced value ticks (max/mid/min) rather than a fixed
    # "nice round number" step — simpler, and always lands exactly on
    # the data's own range regardless of scale.
    y_ticks = [
        {"y": y_at(value), "label": f"{value:,.0f}"}
        for value in (y_max, (y_min + y_max) / 2, y_min)
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "path_d": path_d,
        "coords": coords,
        "labels": [label for label, _ in points],
        "value_labels": [f"{value:,.2f}" for _, value in points],
        "zero_y": zero_y,
        "y_ticks": y_ticks,
    }


def _svg_bar_chart(
    rows: list[tuple[str, Decimal, Decimal]],
    *,
    width: int = 720,
    height: int = 220,
    pad_left: int = 64,
    pad_right: int = 16,
    pad_top: int = 16,
    pad_bottom: int = 28,
) -> dict:
    """Return template-ready SVG geometry for paired income/expense bars.

    ``rows`` is ``(label, income_total, expense_total)`` triples, oldest
    first. Both totals are plotted as magnitudes (``abs``) rising from a
    shared zero baseline — a chart comparing "how much came in" vs "how
    much went out" reads more naturally that way than mixing signed
    values with unsigned bar heights. Returns ``{"has_data": False}``
    for no rows. ``pad_bottom`` is taller than the line chart's, to make
    room for a label under each bar group.
    """
    if not rows:
        return {"has_data": False}

    magnitudes = [abs(float(income)) for _, income, _ in rows] + [
        abs(float(expense)) for _, _, expense in rows
    ]
    y_max = max(magnitudes) or 1.0

    plot_left = pad_left
    plot_right = width - pad_right
    plot_width = plot_right - plot_left
    plot_bottom = height - pad_bottom
    plot_height = plot_bottom - pad_top

    count = len(rows)
    group_width = plot_width / count
    bar_width = group_width * 0.32
    gap = group_width * 0.06

    def bar_height(value: float) -> float:
        return plot_height * (value / y_max)

    bars = []
    for index, (label, income, expense) in enumerate(rows):
        center = plot_left + group_width * (index + 0.5)
        income_h = bar_height(abs(float(income)))
        expense_h = bar_height(abs(float(expense)))
        bars.append(
            {
                "label": label,
                "label_x": center,
                "bar_width": bar_width,
                "income_x": center - gap / 2 - bar_width,
                "income_y": plot_bottom - income_h,
                "income_h": income_h,
                "income_label": f"{abs(income):,.2f}",
                "expense_x": center + gap / 2,
                "expense_y": plot_bottom - expense_h,
                "expense_h": expense_h,
                "expense_label": f"{abs(expense):,.2f}",
            }
        )

    y_ticks = [
        {"y": pad_top, "label": f"{y_max:,.0f}"},
        {"y": plot_bottom, "label": "0"},
    ]

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "plot_bottom": plot_bottom,
        "bars": bars,
        "y_ticks": y_ticks,
    }


@router.get("", response_class=HTMLResponse)
def reports_overview(request: Request) -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary."""
    transactions = read_ledger()
    accounts = read_accounts()
    net_worth_points = net_worth_by_month(transactions, accounts)
    chart = _svg_line_chart([(point.label, point.value) for point in net_worth_points])
    years = yearly_totals_with_yoy(transactions)
    bar_chart = _svg_bar_chart(
        [(str(y.year), y.income_total, y.expense_total) for y in reversed(years)]
    )
    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {"years": years, "chart": chart, "bar_chart": bar_chart},
    )


@router.get("/{year}", response_class=HTMLResponse)
def year_detail(request: Request, year: int) -> HTMLResponse:
    """Render one year's monthly breakdown and income/expense category drill-down."""
    transactions = read_ledger()
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
        },
    )
