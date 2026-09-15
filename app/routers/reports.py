"""Routes for the reporting & visualization views.

Read-only — no forms, no writes. Every view pulls from the shared
``services.aggregation`` group-by layer (CLAUDE.md's "one shared
aggregation layer" invariant) rather than one-off per-view logic.

The net worth chart is rendered as plain inline SVG computed here, not
via a vendored JS charting library — a deliberate choice (see
CLAUDE.md) to keep the app's zero-network-dependency, minimal-JS
posture intact rather than trusting/maintaining a third-party JS file
for a single line chart. ``_svg_line_chart`` is presentational
geometry, not a business rule, so it lives here rather than in
``services`` — a future swap to a JS charting library would only touch
this function and the template, not ``services.aggregation``'s data.
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
    pad: int = 32,
) -> dict:
    """Return template-ready SVG geometry for a line chart of ``points``.

    ``points`` is ``(label, value)`` pairs, oldest first. Returns
    ``{"has_data": False}`` for no points. A flat series (every value
    equal) gets an artificial +/-1 padding on the value range so the
    line doesn't divide by zero.
    """
    if not points:
        return {"has_data": False}

    values = [float(value) for _, value in points]
    y_min, y_max = min(values), max(values)
    if y_min == y_max:
        y_min -= 1
        y_max += 1

    plot_width = width - 2 * pad
    plot_height = height - 2 * pad
    count = len(points)

    def x_at(index: int) -> float:
        if count == 1:
            return pad + plot_width / 2
        return pad + plot_width * index / (count - 1)

    def y_at(value: float) -> float:
        return pad + plot_height * (1 - (value - y_min) / (y_max - y_min))

    coords = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    zero_y = y_at(0) if y_min < 0 < y_max else None

    return {
        "has_data": True,
        "width": width,
        "height": height,
        "path_d": path_d,
        "coords": coords,
        "labels": [label for label, _ in points],
        "value_labels": [f"{value:,.2f}" for _, value in points],
        "zero_y": zero_y,
    }


@router.get("", response_class=HTMLResponse)
def reports_overview(request: Request) -> HTMLResponse:
    """Render the reports landing page: net worth chart + annual summary."""
    transactions = read_ledger()
    accounts = read_accounts()
    net_worth_points = net_worth_by_month(transactions, accounts)
    chart = _svg_line_chart([(point.label, point.value) for point in net_worth_points])
    years = yearly_totals_with_yoy(transactions)
    return templates.TemplateResponse(
        request, "reports/list.html", {"years": years, "chart": chart}
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
