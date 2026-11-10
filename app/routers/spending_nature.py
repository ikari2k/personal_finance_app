"""Routes for the fixed-vs-flexible spending views.

A card for ``/reports`` (monthly stacked bars + averages, range tabs
swapped via HTMX), a detail page listing the recurring-expense groups
``services.recurring`` found, and a review dialog that previews suggested
tags (old -> new) and applies only the ticked rows. Tags live in
``config/expense_nature.toml`` (``storage.expense_nature``). The stacked
bars are inline SVG computed here, like the other report charts.
"""

import json
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.transaction import TransactionType
from app.routers import breadcrumbs
from app.routers.htmx_events import toast
from app.routers.scope import resolve_account
from app.services.aggregation import NatureMonth, nature_monthly_totals
from app.services.categories import category_pair_exists
from app.services.recurring import (
    NATURE_FIXED,
    NATURE_FLEXIBLE,
    NATURE_SEMI,
    NATURES,
    RecurringGroup,
    detect_recurring,
    effective_nature,
    suggest_natures,
)
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories
from app.storage.expense_nature import NatureTags, read_nature_tags, write_nature_tags
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/reports/fixed-flexible", tags=["reports"])

NATURE_LABELS = {
    NATURE_FIXED: "Fixed",
    NATURE_SEMI: "Semi-fixed",
    NATURE_FLEXIBLE: "Flexible",
    "": "Untagged",
}
PERIOD_LABELS = {
    7: "Weekly",
    14: "Every 2 weeks",
    30: "Monthly",
    60: "Every 2 months",
    91: "Quarterly",
    365: "Yearly",
}
CARD_RANGES = (3, 6, 12)
DEFAULT_CARD_RANGE = 6
_CHART_W, _CHART_H = 560, 220
_PAD_TOP, _PAD_BOTTOM = 22, 26


def _stacked_bars(months: list[NatureMonth]) -> list[dict]:
    """Return one geometry dict per month: segment rects stacked from the baseline."""
    peak = max((m.total for m in months), default=Decimal("0"))
    plot_h = _CHART_H - _PAD_TOP - _PAD_BOTTOM
    slot = _CHART_W / len(months)
    width = min(44.0, slot * 0.6)
    bars = []
    for index, month in enumerate(months):
        x = slot * index + (slot - width) / 2
        y = _CHART_H - _PAD_BOTTOM
        segments = []
        for nature in (NATURE_FIXED, NATURE_SEMI, NATURE_FLEXIBLE, ""):
            amount = month.spend[nature]
            if not amount or not peak:
                continue
            height = plot_h * float(amount / peak)
            y -= height
            segments.append(
                {
                    "nature": nature or "untagged",
                    "y": round(y, 1),
                    "h": round(height, 1),
                    "title": f"{month.label} {NATURE_LABELS[nature]}: {amount:,.2f}",
                }
            )
        bars.append(
            {
                "x": round(x, 1),
                "w": round(width, 1),
                "cx": round(x + width / 2, 1),
                "label": month.label.split(" ")[0],
                "segments": segments,
            }
        )
    return bars


def _year_dividers(months: list[NatureMonth]) -> list[dict]:
    """Return a divider (x position and both years) wherever the year changes."""
    slot = _CHART_W / len(months)
    return [
        {
            "x": round(slot * index, 1),
            "before": months[index - 1].key[:4],
            "after": month.key[:4],
        }
        for index, month in enumerate(months)
        if index and month.key[:4] != months[index - 1].key[:4]
    ]


def nature_card_context(
    transactions: list, tags: NatureTags, today: date, months: int
) -> dict:
    """Build the template context for the ``/reports`` fixed-vs-flexible card."""
    series = nature_monthly_totals(transactions, tags, today, months)
    totals = {
        nature: sum((m.spend[nature] for m in series), Decimal("0"))
        for nature in (*NATURES, "")
    }
    spend = sum(totals.values(), Decimal("0"))
    income = sum((m.income for m in series), Decimal("0"))
    rows = [
        {
            "nature": nature or "untagged",
            "label": NATURE_LABELS[nature],
            "average": totals[nature] / months,
            "share": float(totals[nature] / spend * 100) if spend else 0.0,
        }
        for nature in (*NATURES, "")
        if nature or totals[nature]
    ]
    return {
        "has_spend": bool(spend),
        "tagged": bool(spend - totals[""]),
        "rows": rows,
        "committed_pct": (
            float(totals[NATURE_FIXED] / income * 100) if income > 0 else None
        ),
        "bars": _stacked_bars(series),
        "dividers": _year_dividers(series),
        "width": _CHART_W,
        "height": _CHART_H,
        "baseline": _CHART_H - _PAD_BOTTOM,
        "months": months,
        "ranges": CARD_RANGES,
    }


def _scoped(request: Request, account_id: str | None) -> tuple[str, list]:
    """Return the resolved account id and the ledger filtered to it."""
    account_id = resolve_account(request, account_id)
    ledger = read_ledger()
    if account_id:
        ledger = [t for t in ledger if t.account_id == account_id]
    return account_id, ledger


@router.get("/card", response_class=HTMLResponse)
def card_fragment(
    request: Request, months: int = DEFAULT_CARD_RANGE, account_id: str | None = None
) -> HTMLResponse:
    """Render just the card for one range tab (an HTMX swap target)."""
    account_id, ledger = _scoped(request, account_id)
    if months not in CARD_RANGES:
        months = DEFAULT_CARD_RANGE
    return templates.TemplateResponse(
        request,
        "reports/_fixed_flexible.html",
        {
            "nature_card": nature_card_context(
                ledger, read_nature_tags(), date.today(), months
            ),
            "account_id": account_id,
        },
    )


def _table_context(ledger: list, tags: NatureTags, account_id: str) -> dict:
    """Return the detected groups, each paired with the tag it currently resolves to."""
    groups = detect_recurring(ledger, date.today())
    return {
        "rows": [
            (g, effective_nature(tags, g.category, g.subcategory)) for g in groups
        ],
        "hidden_count": sum(1 for g in groups if g.verdict == NATURE_FLEXIBLE),
        "nature_labels": NATURE_LABELS,
        "period_labels": PERIOD_LABELS,
        "account_id": account_id,
    }


def _render_table(
    request: Request,
    ledger: list,
    account_id: str,
    *,
    oob: bool = False,
    show_flexible: bool = False,
    headers=None,
):
    """Render the recurring table fragment (``oob`` for a dialog's out-of-band swap)."""
    return templates.TemplateResponse(
        request,
        "fixed_flexible/_table.html",
        {
            **_table_context(ledger, read_nature_tags(), account_id),
            "oob": oob,
            "show_flexible": show_flexible,
        },
        headers=headers,
    )


def _suggestion_rows(
    groups: list[RecurringGroup], tags: NatureTags, expense_tree: dict
) -> list[dict]:
    """Suggested tag changes for existing expense subcategories, biggest spend first.

    A suggestion equal to the tag the subcategory already resolves to is
    omitted. A row that would overwrite a tag the user already set starts
    unticked, so applying never silently overrides a choice.
    """
    spend: dict[tuple[str, str], Decimal] = {}
    for group in groups:
        pair = (group.category, group.subcategory)
        spend[pair] = spend.get(pair, Decimal("0")) + group.spend
    rows = []
    for (category, subcategory), nature in suggest_natures(groups).items():
        entry = expense_tree.get(category)
        if entry is None or (subcategory and subcategory not in entry["subcategories"]):
            continue
        current = effective_nature(tags, category, subcategory)
        if current == nature:
            continue
        rows.append(
            {
                "category": category,
                "subcategory": subcategory,
                "now": current,
                "suggested": nature,
                "checked": not current,
                "spend": spend[(category, subcategory)],
                "value": json.dumps([category, subcategory, nature]),
            }
        )
    rows.sort(key=lambda r: -r["spend"])
    return rows


@router.get("", response_class=HTMLResponse)
def detail_page(request: Request, account_id: str | None = None) -> HTMLResponse:
    """Render the recurring-expense detection page."""
    account_id, ledger = _scoped(request, account_id)
    return templates.TemplateResponse(
        request,
        "fixed_flexible/list.html",
        {
            **_table_context(ledger, read_nature_tags(), account_id),
            "oob": False,
            "show_flexible": False,
            "accounts": read_accounts(),
            "account_id": account_id,
            "breadcrumbs": [
                breadcrumbs.Crumb("Reports", "/reports"),
                breadcrumbs.Crumb("Fixed vs flexible", None),
            ],
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_dialog(request: Request, account_id: str | None = None) -> HTMLResponse:
    """Render the suggestions dialog: current tag -> suggested tag per subcategory."""
    account_id, ledger = _scoped(request, account_id)
    groups = detect_recurring(ledger, date.today())
    rows = _suggestion_rows(
        groups, read_nature_tags(), read_categories().get("expense", {})
    )
    return templates.TemplateResponse(
        request,
        "fixed_flexible/_review.html",
        {"rows": rows, "nature_labels": NATURE_LABELS, "account_id": account_id},
    )


@router.post("/apply", response_class=HTMLResponse)
def apply_suggestions(
    request: Request,
    pick: list[int] = Form(default_factory=list),
    row_category: list[str] = Form(default_factory=list),
    row_subcategory: list[str] = Form(default_factory=list),
    row_nature: list[str] = Form(default_factory=list),
    account_id: str = Form(""),
) -> HTMLResponse:
    """Write the ticked rows as tags; the server re-checks every pair.

    The review form posts one hidden category/subcategory and one nature
    ``<select>`` per row (the user may change the suggested value) plus
    the indices of the ticked rows in ``pick``.
    """
    tags = read_nature_tags()
    expense_tree = read_categories()
    applied = 0
    for index in pick:
        if (
            not 0
            <= index
            < min(len(row_category), len(row_subcategory), len(row_nature))
        ):
            continue
        category, subcategory, nature = (
            row_category[index],
            row_subcategory[index],
            row_nature[index],
        )
        if nature not in NATURES or not category_pair_exists(
            expense_tree, TransactionType.EXPENSE, category, subcategory
        ):
            continue
        tags[(category, subcategory)] = nature
        applied += 1
    if applied:
        write_nature_tags(tags)
    account_id, ledger = _scoped(request, account_id)
    noun = "tag" if applied == 1 else "tags"
    return _render_table(
        request,
        ledger,
        account_id,
        oob=True,
        headers=toast(f"Applied {applied} {noun}", close_dialog=True),
    )


@router.post("/tag", response_class=HTMLResponse)
def set_tag(
    request: Request,
    category: str = Form(...),
    subcategory: str = Form(""),
    nature: str = Form(""),
    show_flexible: bool = Form(False),
    account_id: str = Form(""),
) -> HTMLResponse:
    """Set (or, with a blank ``nature``, clear) one category/subcategory's tag.

    Backs the table's inline tag select. Re-renders the table, keeping the
    "show flexible" toggle as the client had it.
    """
    tags = read_nature_tags()
    if nature in NATURES or nature == "":
        if category_pair_exists(
            read_categories(), TransactionType.EXPENSE, category, subcategory
        ):
            if nature:
                tags[(category, subcategory)] = nature
            else:
                tags.pop((category, subcategory), None)
            write_nature_tags(tags)
    account_id, ledger = _scoped(request, account_id)
    return _render_table(request, ledger, account_id, show_flexible=show_flexible)
