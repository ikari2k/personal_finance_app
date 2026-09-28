"""Route for the at-a-glance dashboard landing page.

Read-only, like ``reports.py`` — no forms of its own beyond the three
quick-add dialogs it shares with ``/transactions``/``/transfers`` (same
routes, same dialog markup, reused verbatim rather than duplicated logic).
Every number pulls from the existing ``services.aggregation``/``services
.balances``/``services.transactions`` layers; this module only assembles
them into one page and draws the one small piece of new presentation
geometry (the net-worth sparkline) the same way ``reports.py`` draws its
own charts — plain inline SVG, no JS charting library.

Known simplification: the quick-add dialogs post back to
``/transactions``/``/transfers``, whose success response tries to refresh
``#transactions-table-wrapper`` out-of-band — a element this page doesn't
have, so that swap silently no-ops. The dialog still closes and the toast
still fires (both driven by the ``HX-Trigger`` header, independent of the
oob swap), so adding a transaction from the dashboard works correctly;
its own widgets (recent transactions, this month's totals, net worth)
just don't live-update until the next full page load. Not worth extra
plumbing for a dashboard that's typically loaded fresh each visit.
"""

from datetime import date
from decimal import Decimal
from itertools import groupby

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.models.account import ACCOUNT_TYPE_LABELS, AccountType
from app.models.transaction import TransactionType
from app.routers.reports import _category_config, _ring_geometry
from app.services.aggregation import (
    UNCATEGORIZED,
    category_totals_for_month,
    monthly_totals_with_mom,
    net_worth_by_month,
    rolling_average_monthly_expense,
)
from app.services.balances import all_balances
from app.services.transactions import (
    find_orphan_transfer_candidates,
    find_transfer_matches,
)
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(tags=["dashboard"])

ACCOUNT_TYPE_ICONS: dict[AccountType, str] = {
    AccountType.CHECKING: "bank",
    AccountType.SAVINGS: "piggy-bank",
    AccountType.CREDIT_CARD: "credit-card",
    AccountType.CASH: "wallet",
    AccountType.INVESTMENT: "trending-up",
    AccountType.OTHER: "bank",
}

# How many categories/rows each widget shows — a dashboard summarizes,
# it doesn't replace the full /reports or /transactions views a widget
# links out to.
BUDGET_ATTENTION_LIMIT = 5
# Independent of _ring_geometry's own 75%/100% color-tier boundaries
# (which also drive /reports' ring colors and must stay put) — this is
# just the dashboard widget's own "worth surfacing here" cutoff.
BUDGET_ATTENTION_THRESHOLD_PCT = 60
TOP_CATEGORIES_LIMIT = 10
# The most recent transactions widget groups by calendar day rather than
# capping at a flat row count — RECENT_DAYS_LIMIT is the number of most
# recent *distinct dates with any activity* shown, not calendar days
# (a weekend with nothing recorded doesn't count against it), matching
# the transactions list's own "group by months with activity, not every
# calendar month" convention.
RECENT_DAYS_LIMIT = 5
SPARKLINE_MONTHS = 6
# Trailing windows (in days) for the "Average monthly spend" widget —
# the standard ~1/3/12-month rolling-average horizons, shortest first so
# the trend comparison below reads "recent pace vs. your longer-run
# baseline" (now a full year, the steadiest baseline available). Each
# window's total is still walked in real calendar days (see
# rolling_average_monthly_expense) and only the displayed figure is a
# monthly-equivalent rate, not the window length itself.
ROLLING_AVERAGE_WINDOWS = (30, 90, 180, 365)


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    """Return the percent change from ``previous`` to ``current``, or ``None``.

    ``None`` when ``previous`` is zero — a percent change against a zero
    base is undefined, not zero, so the template shows no delta rather
    than a misleading "+inf%"/"+0%".
    """
    if not previous:
        return None
    return (current - previous) / abs(previous) * 100


_SPARK_W, _SPARK_H = 460, 96
_SPARK_PAD_RIGHT, _SPARK_PAD_TOP, _SPARK_PAD_BOTTOM = 4, 8, 6
# Reserved on the left for the max/min value labels — a plain sparkline
# with no numbers on its scale can't actually be read, only glanced at
# (see references/marks-and-anatomy.md's "every label names a value the
# chart reaches"), so this trades a slice of plot width for two labels
# instead of leaving the axis silent.
_SPARK_LABEL_GUTTER = 60


def _svg_net_worth_sparkline(values: list[Decimal]) -> str:
    """Return a small inline-SVG area+line sparkline for the last few months.

    Deliberately not ``reports._svg_net_worth_chart`` — that draws a full
    chart with bars, gridlines, and a full axis sized for its own page;
    this is a compact glance-only version for the dashboard hero, same
    "one scale, labeled reach, theme-token colors" discipline at a much
    smaller size, just with only the two labels (max and min) a
    sparkline has room for rather than a full tick axis. A single value
    (or none) draws a flat line instead of dividing by a zero span.
    """
    if not values:
        return ""
    floats = [float(v) for v in values]
    n = len(floats)
    vmin, vmax = min(floats), max(floats)
    vspan = (vmax - vmin) or 1.0
    plot_x0 = _SPARK_LABEL_GUTTER
    plot_x1 = _SPARK_W - _SPARK_PAD_RIGHT

    def xy(i: int, v: float) -> tuple[float, float]:
        x = plot_x0 + (plot_x1 - plot_x0) * (i / (n - 1) if n > 1 else 0.5)
        y = _SPARK_PAD_TOP + (_SPARK_H - _SPARK_PAD_TOP - _SPARK_PAD_BOTTOM) * (
            1 - (v - vmin) / vspan
        )
        return x, y

    points = [xy(i, v) for i, v in enumerate(floats)]
    path_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    floor_y = _SPARK_H - _SPARK_PAD_BOTTOM
    area_d = (
        path_d
        + f" L {points[-1][0]:.1f} {floor_y:.1f} L {points[0][0]:.1f} {floor_y:.1f} Z"
    )
    top_y, bottom_y = _SPARK_PAD_TOP, floor_y
    mid_y = (top_y + bottom_y) / 2
    gridlines = "".join(
        f'<line x1="{plot_x0}" y1="{y:.1f}" x2="{plot_x1}" y2="{y:.1f}" '
        f'class="spark-grid"/>'
        for y in (top_y, mid_y, bottom_y)
    )
    # Top/bottom gridlines land exactly on vmax/vmin (xy()'s own mapping
    # puts the highest value at the top row and the lowest at the
    # bottom), so labeling them there is exact, not an approximation.
    labels = (
        f'<text x="{plot_x0 - 8}" y="{top_y:.1f}" dy="0.32em" text-anchor="end" '
        f'class="spark-axis-label">{vmax:,.0f}</text>'
        f'<text x="{plot_x0 - 8}" y="{bottom_y:.1f}" dy="0.32em" text-anchor="end" '
        f'class="spark-axis-label">{vmin:,.0f}</text>'
    )
    last_x, last_y = points[-1]
    return (
        f'<svg viewBox="0 0 {_SPARK_W} {_SPARK_H}" aria-label="Net worth trend">'
        f'<defs><linearGradient id="nwFill" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" class="spark-fill-start"/>'
        f'<stop offset="100%" class="spark-fill-end"/></linearGradient></defs>'
        f"{gridlines}"
        f'<path d="{area_d}" fill="url(#nwFill)" stroke="none"/>'
        f'<path d="{path_d}" fill="none" class="spark-line" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.2" class="spark-dot"/>'
        f"{labels}"
        f"</svg>"
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Render the dashboard: net worth, this month, accounts, budgets, activity."""
    accounts = read_accounts()
    ledger = read_ledger()
    categories = read_categories()
    balances = all_balances(accounts, ledger)

    # --- net worth + sparkline ---
    net_worth_points = net_worth_by_month(ledger, accounts)
    current_net_worth = (
        net_worth_points[-1].value
        if net_worth_points
        else sum((a.starting_balance for a in accounts), Decimal("0"))
    )
    previous_net_worth = (
        net_worth_points[-2].value if len(net_worth_points) >= 2 else None
    )
    net_worth_delta = (
        current_net_worth - previous_net_worth
        if previous_net_worth is not None
        else None
    )
    net_worth_delta_pct = (
        _pct_change(current_net_worth, previous_net_worth)
        if previous_net_worth is not None
        else None
    )
    sparkline_points = net_worth_points[-SPARKLINE_MONTHS:]
    sparkline_svg = _svg_net_worth_sparkline([p.value for p in sparkline_points])

    # --- this month vs last month ---
    today = date.today()
    monthly_by_key = {m.key: m for m in monthly_totals_with_mom(ledger)}
    this_month_key = f"{today.year:04d}-{today.month:02d}"
    prev_year, prev_month = (
        (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    )
    prev_month_key = f"{prev_year:04d}-{prev_month:02d}"
    this_month = monthly_by_key.get(this_month_key)
    prev_month_total = monthly_by_key.get(prev_month_key)
    income_total = this_month.income_total if this_month else Decimal("0")
    # MonthlyTotal.expense_total is signed (negative), matching the
    # transactions list's own type-group-subtotal convention — shown as
    # a magnitude here instead, a more natural read for a stat tile
    # ("Expense: 9,340.55", not "-9,340.55"). net_total is taken
    # straight from MonthlyTotal rather than re-derived from
    # income/expense here, so it can't drift from the signed arithmetic
    # services.aggregation already gets right.
    expense_total = abs(this_month.expense_total) if this_month else Decimal("0")
    net_total = this_month.net_total if this_month else Decimal("0")
    prev_income_total = prev_month_total.income_total if prev_month_total else None
    prev_expense_total = (
        abs(prev_month_total.expense_total) if prev_month_total else None
    )
    income_delta_pct = (
        _pct_change(income_total, prev_income_total)
        if prev_income_total is not None
        else None
    )
    expense_delta_pct = (
        _pct_change(expense_total, prev_expense_total)
        if prev_expense_total is not None
        else None
    )

    # --- rolling average monthly spend (30/90/180-day windows) ---
    rolling_averages = [
        {
            "days": window,
            "value": rolling_average_monthly_expense(ledger, today, window),
        }
        for window in ROLLING_AVERAGE_WINDOWS
    ]
    # Recent pace (the shortest window) vs. the longer-run baseline (the
    # longest one) — above baseline reads as "spending faster lately",
    # same sense as the reports pages' own movers lists, not a plain
    # sign-based color.
    shortest_avg = rolling_averages[0]["value"]
    longest_avg = rolling_averages[-1]["value"]
    rolling_average_trend_pct = (
        _pct_change(shortest_avg, longest_avg)
        if shortest_avg is not None and longest_avg is not None
        else None
    )

    # --- accounts ---
    active_accounts = [a for a in accounts if a.status.value == "active"]

    # --- budget status (category-level only, expense side) ---
    category_config = _category_config(categories, TransactionType.EXPENSE)
    month_categories = category_totals_for_month(
        ledger, today.year, today.month, TransactionType.EXPENSE
    )
    budget_rows = []
    for cat in month_categories:
        budget = category_config.get(cat.name, {}).get("budget")
        ring = _ring_geometry(cat.total, budget)
        if ring is None or not budget:
            continue
        pct = float(cat.total / budget * 100)
        if pct < BUDGET_ATTENTION_THRESHOLD_PCT:
            continue
        budget_rows.append(
            {
                "name": cat.name,
                "icon": category_config.get(cat.name, {}).get("icon", ""),
                "amount": cat.total,
                "budget": budget,
                "ring": ring,
                "pct": pct,
            }
        )
    budget_rows.sort(key=lambda row: row["pct"], reverse=True)
    budget_rows = budget_rows[:BUDGET_ATTENTION_LIMIT]

    # --- top categories this month ---
    top_categories = [
        {
            "name": cat.name,
            "icon": category_config.get(cat.name, {}).get("icon", ""),
            "amount": cat.total,
        }
        for cat in month_categories[:TOP_CATEGORIES_LIMIT]
    ]
    top_category_max = max((c["amount"] for c in top_categories), default=Decimal("0"))

    # --- needs attention ---
    uncategorized_count = sum(
        1
        for t in ledger
        if t.category == UNCATEGORIZED and t.type is not TransactionType.TRANSFER
    )
    transfer_candidate_count = len(find_transfer_matches(ledger, accounts)) + len(
        find_orphan_transfer_candidates(ledger, accounts)
    )

    # --- recent transactions, grouped by day ---
    income_config = _category_config(categories, TransactionType.INCOME)
    expense_config = _category_config(categories, TransactionType.EXPENSE)
    sorted_desc = sorted(ledger, key=lambda t: (t.date, t.id), reverse=True)
    recent_days = []
    for txn_date, day_transactions in groupby(sorted_desc, key=lambda t: t.date):
        if len(recent_days) >= RECENT_DAYS_LIMIT:
            break
        rows = []
        for t in day_transactions:
            icon = ""
            if t.type is TransactionType.INCOME:
                icon = income_config.get(t.category, {}).get("icon", "")
            elif t.type is TransactionType.EXPENSE:
                icon = expense_config.get(t.category, {}).get("icon", "")
            rows.append(
                {
                    "txn": t,
                    "icon": icon,
                    "account_name": next(
                        (a.name for a in accounts if a.id == t.account_id),
                        t.account_id,
                    ),
                }
            )
        recent_days.append(
            {"date": txn_date, "label": txn_date.strftime("%a, %b %d"), "rows": rows}
        )

    return templates.TemplateResponse(
        request,
        "dashboard/list.html",
        {
            "as_of": today,
            "current_net_worth": current_net_worth,
            "net_worth_delta": net_worth_delta,
            "net_worth_delta_pct": net_worth_delta_pct,
            "sparkline_svg": sparkline_svg,
            "sparkline_labels": [p.label for p in sparkline_points],
            "income_total": income_total,
            "expense_total": expense_total,
            "net_total": net_total,
            "income_delta_pct": income_delta_pct,
            "expense_delta_pct": expense_delta_pct,
            "rolling_averages": rolling_averages,
            "rolling_average_trend_pct": rolling_average_trend_pct,
            "active_accounts": active_accounts,
            "balances": balances,
            "account_type_labels": ACCOUNT_TYPE_LABELS,
            "account_type_icons": ACCOUNT_TYPE_ICONS,
            "budget_rows": budget_rows,
            "budget_threshold_pct": BUDGET_ATTENTION_THRESHOLD_PCT,
            "top_categories": top_categories,
            "top_category_max": top_category_max,
            "uncategorized_count": uncategorized_count,
            "transfer_candidate_count": transfer_candidate_count,
            "recent_days": recent_days,
        },
    )
