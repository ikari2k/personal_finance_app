"""Routes for the 50/30/20 budget-rule report and its editable targets.

Read-only report plus one dialog form (the target percentages). All the
numbers come from ``services.aggregation.budget_rule_split`` /
``budget_rule_monthly_series``; this module only picks periods, adds the
targets comparison, and draws the two small inline-SVG charts (a stacked
split bar per period and a 12-month trend) the same way ``reports.py``
draws its own.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models.budget_rule import BudgetRuleTargets
from app.models.category import CategoriesByType
from app.routers import breadcrumbs
from app.routers.htmx_events import toast
from app.services.aggregation import (
    BudgetRuleMonth,
    BudgetRuleSplit,
    budget_rule_monthly_series,
    budget_rule_split,
    top_unclassified_spend,
)
from app.services.budget_rule import validate_targets
from app.storage.accounts import read_accounts
from app.storage.budget_rule import read_targets, write_targets
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports/budget-rule", tags=["reports"])

TREND_MONTHS = 12
ROLLING_WINDOWS = (3, 6, 12)
BAR_WIDTH = 600


@dataclass
class BucketRow:
    """One bucket's line in a period's table: amount, share, target, verdict."""

    key: str
    label: str
    amount: Decimal
    pct: Decimal | None
    target: int | None
    delta: Decimal | None
    status: str


def _parse_month(raw: str, today: date) -> tuple[int, int]:
    """Parse ``YYYY-MM`` (blank -> current month); 404 on anything else."""
    if not raw:
        return today.year, today.month
    try:
        year_str, month_str = raw.split("-")
        year, month = int(year_str), int(month_str)
        date(year, month, 1)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid month") from exc
    return year, month


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _rows(split: BudgetRuleSplit, targets: BudgetRuleTargets) -> list[BucketRow]:
    """Build the per-bucket table rows, judging each against its target."""

    def row(key, label, amount, pct, target, over_is_bad):
        delta = pct - target if pct is not None and target is not None else None
        if delta is None:
            status = ""
        elif over_is_bad:
            status = "over target" if delta > 0 else "within target"
        else:
            status = "below target" if delta < 0 else "on target"
        return BucketRow(key, label, amount, pct, target, delta, status)

    return [
        row("needs", "Needs", split.needs, split.needs_pct, targets.needs, True),
        row("wants", "Wants", split.wants, split.wants_pct, targets.wants, True),
        row(
            "savings",
            "Savings",
            split.savings,
            split.savings_pct,
            targets.savings,
            False,
        ),
        row(
            "unclassified",
            "Unclassified",
            split.unclassified,
            split.unclassified_pct,
            None,
            True,
        ),
    ]


def _split_bar(
    split: BudgetRuleSplit, targets: BudgetRuleTargets, compact: bool = False
) -> dict | None:
    """Geometry for one period's stacked bar, or ``None`` without income.

    Segments run Needs, Wants, Savings, Unclassified so the dashed target
    markers (Needs, Needs+Wants) line up with the bar's boundaries when
    spending matches the rule. The scale stretches past 100% when spend
    plus savings exceeds income. Negative savings draws no segment (the
    table carries the number). ``compact`` gives a slim bar with no marker
    labels, for stacking several on a summary page.
    """
    if split.needs_pct is None:
        return None
    parts = [
        ("needs", "Needs", split.needs_pct),
        ("wants", "Wants", split.wants_pct),
        ("savings", "Savings", max(split.savings_pct, Decimal("0"))),
        ("unclassified", "Unclassified", split.unclassified_pct),
    ]
    scale_pct = max(Decimal("100"), sum((p[2] for p in parts), Decimal("0")))
    per_pct = BAR_WIDTH / float(scale_pct)
    segments = []
    x = 0.0
    for key, label, pct in parts:
        width = max(float(pct), 0.0) * per_pct
        if width > 0:
            segments.append(
                {"key": key, "label": f"{label} {pct}%", "x": x, "width": width}
            )
        x += width
    markers = [
        {"x": targets.needs * per_pct, "label": f"{targets.needs}%"},
        {
            "x": (targets.needs + targets.wants) * per_pct,
            "label": f"{targets.needs + targets.wants}%",
        },
    ]
    return {
        "width": BAR_WIDTH,
        "height": 26 if compact else 64,
        "bar_y": 3 if compact else 12,
        "bar_h": 20 if compact else 28,
        "marker_y1": 0 if compact else 6,
        "marker_y2": 26 if compact else 46,
        "label_y": None if compact else 60,
        "segments": segments,
        "markers": markers,
        "income_x": 100 * per_pct,
    }


def _tick_step(span: float) -> int:
    return 20 if span <= 120 else 50 if span <= 300 else 100


def _trend_chart(months: list[BudgetRuleMonth]) -> dict | None:
    """Geometry for the stacked % of income trend; ``None`` with no months."""
    if not months:
        return None
    width, height = 720, 260
    pad_left, pad_right, pad_top, pad_bottom = 44, 12, 12, 30
    stacks = []
    for m in months:
        s = m.split
        if s.needs_pct is None:
            stacks.append(None)
            continue
        positives = [
            s.needs_pct,
            s.wants_pct,
            max(s.savings_pct, 0),
            s.unclassified_pct,
        ]
        stacks.append(
            (positives, min(s.savings_pct, Decimal("0")), sum(positives, Decimal("0")))
        )
    top = max([100.0] + [float(st[2]) for st in stacks if st])
    bottom = min([0.0] + [float(st[1]) for st in stacks if st])
    step = _tick_step(top - bottom)
    y_max = step * math.ceil(top / step)
    y_min = -step * math.ceil(-bottom / step)
    plot_h = height - pad_top - pad_bottom
    plot_w = width - pad_left - pad_right
    slot = plot_w / len(months)
    bar_w = slot * 0.6

    def y_at(v: float) -> float:
        return pad_top + plot_h * (1 - (v - y_min) / (y_max - y_min))

    bars, labels = [], []
    for i, (m, st) in enumerate(zip(months, stacks)):
        x = pad_left + slot * i + (slot - bar_w) / 2
        center = x + bar_w / 2
        labels.append(
            {
                "x": center,
                "text": m.label[:3],
                "year": m.label[-4:] if m.key.endswith("-01") or i == 0 else "",
            }
        )
        if st is None:
            bars.append(
                {"x": x, "w": bar_w, "segments": [], "title": f"{m.label}: no income"}
            )
            continue
        positives, negative, _ = st
        segments, cursor = [], 0.0
        for key, pct in zip(("needs", "wants", "savings", "unclassified"), positives):
            top_v = cursor + float(pct)
            if pct > 0:
                segments.append(
                    {"key": key, "y": y_at(top_v), "h": y_at(cursor) - y_at(top_v)}
                )
            cursor = top_v
        if negative < 0:
            segments.append(
                {"key": "savings", "y": y_at(0), "h": y_at(float(negative)) - y_at(0)}
            )
        s = m.split
        bars.append(
            {
                "x": x,
                "w": bar_w,
                "segments": segments,
                "title": (
                    f"{m.label}: needs {s.needs_pct}%, wants {s.wants_pct}%, "
                    f"savings {s.savings_pct}%, unclassified {s.unclassified_pct}%"
                ),
            }
        )
    ticks = [
        {"y": y_at(v), "label": f"{v}%"}
        for v in range(int(y_min), int(y_max) + 1, step)
    ]
    return {
        "width": width,
        "height": height,
        "plot_left": pad_left,
        "plot_right": width - pad_right,
        "zero_y": y_at(0),
        "bars": bars,
        "labels": labels,
        "label_y": height - 10,
        "ticks": ticks,
    }


def period_summary(
    transactions: list,
    categories: CategoriesByType,
    accounts: list,
    targets: BudgetRuleTargets,
    start: date | None,
    end: date | None,
    *,
    compact: bool = False,
) -> dict:
    """Compute one period's split, bar geometry, table rows and unclassified spend."""
    split = budget_rule_split(transactions, categories, accounts, start, end)
    return {
        "split": split,
        "bar": _split_bar(split, targets, compact),
        "rows": _rows(split, targets),
        "unclassified": top_unclassified_spend(transactions, categories, start, end),
    }


def month_summary(
    transactions: list,
    categories: CategoriesByType,
    accounts: list,
    year: int,
    month: int,
    *,
    compact: bool = False,
) -> dict:
    """Summary of one calendar month, labelled and linked to its full report."""
    return {
        "label": f"{calendar.month_name[month]} {year}",
        "link": f"/reports/budget-rule?month={year:04d}-{month:02d}",
        **period_summary(
            transactions,
            categories,
            accounts,
            read_targets(),
            date(year, month, 1),
            date(year, month, calendar.monthrange(year, month)[1]),
            compact=compact,
        ),
    }


def month_pair(
    transactions: list,
    categories: CategoriesByType,
    accounts: list,
    year: int,
    month: int,
    *,
    compact: bool = False,
) -> list[dict]:
    """Summaries of ``year``-``month`` and the month before it, in that order.

    Shown together because pay often lands late in the month, which makes
    the current month alone look overspent until it does.
    """
    previous_year, previous_month = _shift_month(year, month, -1)
    return [
        month_summary(transactions, categories, accounts, y, m, compact=compact)
        for y, m in ((year, month), (previous_year, previous_month))
    ]


def recent_months(
    transactions: list,
    categories: CategoriesByType,
    accounts: list,
    year: int,
    month: int,
    count: int = 6,
) -> list[dict]:
    """Compact summaries of the last ``count`` months, newest first.

    Starts at ``year``-``month`` (the current month, still in progress).
    Labels are short (``Oct 2026``) so a row's label column stays narrow.
    """
    summaries = []
    for back in range(count):
        y, m = _shift_month(year, month, -back)
        summary = month_summary(transactions, categories, accounts, y, m, compact=True)
        summary["label"] = f"{calendar.month_abbr[m]} {y}"
        summaries.append(summary)
    return summaries


def rolling_summaries(
    transactions: list,
    categories: CategoriesByType,
    accounts: list,
    year: int,
    month: int,
) -> list[dict]:
    """Compact summaries of the last 3/6/12 months ending at ``year``-``month``."""
    targets = read_targets()
    end = date(year, month, calendar.monthrange(year, month)[1])
    results = []
    for months in ROLLING_WINDOWS:
        first_year, first_month = _shift_month(year, month, -(months - 1))
        summary = period_summary(
            transactions,
            categories,
            accounts,
            targets,
            date(first_year, first_month, 1),
            end,
            compact=True,
        )
        results.append({"label": f"Last {months} months", **summary})
    return results


def _content_context(month_key: str, today: date) -> dict:
    """Assemble everything ``budget_rule/_content.html`` renders."""
    year, month = _parse_month(month_key, today)
    transactions = read_ledger()
    categories = read_categories()
    accounts = read_accounts()
    targets = read_targets()

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    periods = []
    windows = [
        ("month", "This month", f"{calendar.month_name[month]} {year}", month_start),
        ("ytd", "Year to date", f"{year} year to date", date(year, 1, 1)),
    ]
    for months in ROLLING_WINDOWS:
        first_year, first_month = _shift_month(year, month, -(months - 1))
        windows.append(
            (
                f"r{months}",
                f"Last {months} mo",
                f"Last {months} months to {calendar.month_name[month]} {year}",
                date(first_year, first_month, 1),
            )
        )
    windows.append(("all", "All time", "All time", None))
    for key, tab, label, start in windows:
        end = month_end
        periods.append(
            {
                "key": key,
                "tab": tab,
                "label": label,
                **period_summary(
                    transactions, categories, accounts, targets, start, end
                ),
            }
        )

    selected = f"{year:04d}-{month:02d}"
    series = [
        m
        for m in budget_rule_monthly_series(transactions, categories, accounts, today)
        if m.key <= selected
    ][-TREND_MONTHS:]
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    return {
        "year": year,
        "month": month,
        "month_key": selected,
        "current_key": f"{today.year:04d}-{today.month:02d}",
        "label": f"{calendar.month_name[month]} {year}",
        "targets": targets,
        "periods": periods,
        "trend": _trend_chart(series),
        "prev_key": f"{prev_year:04d}-{prev_month:02d}",
        "prev_label": f"{calendar.month_name[prev_month]} {prev_year}",
        "next_key": (
            f"{next_year:04d}-{next_month:02d}"
            if (next_year, next_month) <= (today.year, today.month)
            else None
        ),
        "next_label": f"{calendar.month_name[next_month]} {next_year}",
    }


@router.get("", response_class=HTMLResponse)
def budget_rule_page(request: Request, month: str = "") -> HTMLResponse:
    """Render the 50/30/20 report for ``month`` (``YYYY-MM``, default current)."""
    context = _content_context(month, date.today())
    context["breadcrumbs"] = [
        breadcrumbs.Crumb("Reports", "/reports"),
        breadcrumbs.Crumb("50/30/20", None),
    ]
    return templates.TemplateResponse(request, "budget_rule/page.html", context)


def _targets_form(
    request: Request,
    month: str,
    values: dict[str, str],
    error: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "budget_rule/_targets_form.html",
        {"month": month, "values": values, "error": error},
    )


@router.get("/targets/edit", response_class=HTMLResponse)
def edit_targets_form(request: Request, month: str = "") -> HTMLResponse:
    """Render the "edit targets" form for the dialog."""
    targets = read_targets()
    return _targets_form(
        request,
        month,
        {
            "needs": str(targets.needs),
            "wants": str(targets.wants),
            "savings": str(targets.savings),
        },
    )


@router.post("/targets", response_class=HTMLResponse)
def save_targets(
    request: Request,
    needs: str = Form(""),
    wants: str = Form(""),
    savings: str = Form(""),
    month: str = Form(""),
) -> HTMLResponse:
    """Validate and save the targets; refresh the report out-of-band on success."""
    values = {"needs": needs, "wants": wants, "savings": savings}
    try:
        try:
            targets = BudgetRuleTargets(
                needs=int(needs), wants=int(wants), savings=int(savings)
            )
        except ValueError as exc:
            raise ValueError("targets must be whole numbers") from exc
        validate_targets(targets)
    except ValueError as exc:
        return _targets_form(request, month, values, error=str(exc))
    write_targets(targets)
    context = _content_context(month, date.today())
    return templates.TemplateResponse(
        request,
        "budget_rule/_content.html",
        {**context, "oob": True},
        headers=toast("Targets saved", close_dialog=True),
    )
