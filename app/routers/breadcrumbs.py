"""Shared breadcrumb-trail and transactions-link helpers for the reports pages.

Not a router — imported by both ``app.routers.reports`` (to build each
report page's own "Reports > ..." trail, and the outbound links each page
offers into ``/transactions``) and ``app.routers.transactions`` (to
reconstruct that same trail on the transactions list itself, purely from
whichever account/category/subcategory/type/date filters are already in
effect — see ``from_transaction_filters``). Same "shared helper, not a
router" precedent as ``app.routers.htmx_events``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from app.models.transaction import TransactionType


@dataclass(frozen=True)
class Crumb:
    """One breadcrumb segment. ``url`` is ``None`` for the current page."""

    label: str
    url: str | None


def transactions_link(
    *,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    subcategory: str = "",
    txn_type: str = "",
    account_id: str = "",
) -> str:
    """Build a ``/transactions`` URL carrying only the filters actually set.

    Every cross-page link into the transactions list in this app follows
    this same "only non-empty filters appear in the querystring" shape,
    centralized here so a future filter needs updating in one place, not
    at every call site that links out to the ledger.
    """
    params = {
        "date_from": date_from,
        "date_to": date_to,
        "category": category,
        "subcategory": subcategory,
        "txn_type": txn_type,
        "account_id": account_id,
    }
    query = "&".join(f"{key}={quote(value)}" for key, value in params.items() if value)
    return f"/transactions?{query}" if query else "/transactions"


def _report_link(path: str, account_id: str) -> str:
    return f"{path}?account_id={quote(account_id)}" if account_id else path


def for_year(year: int, account_id: str = "") -> list[Crumb]:
    """Breadcrumb trail for ``/reports/{year}``."""
    return [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb(str(year), None),
    ]


def for_month(year: int, month: int, account_id: str = "") -> list[Crumb]:
    """Breadcrumb trail for ``/reports/{year}/{month}``."""
    return [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb(str(year), _report_link(f"/reports/{year}", account_id)),
        Crumb(calendar.month_name[month], None),
    ]


def for_category(category: str, account_id: str = "") -> list[Crumb]:
    """Breadcrumb trail for ``/reports/category``."""
    return [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb(category, None),
    ]


def _month_span(date_from: str, date_to: str) -> tuple[int, int] | None:
    """Return ``(year, month)`` if the range is exactly that month's first/last day."""
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        return None
    if d_from.day != 1:
        return None
    last_day = calendar.monthrange(d_from.year, d_from.month)[1]
    if d_to != date(d_from.year, d_from.month, last_day):
        return None
    return d_from.year, d_from.month


def _year_span(date_from: str, date_to: str) -> int | None:
    """Return the year if the range is exactly that year's Jan 1-Dec 31."""
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        return None
    if (d_from.month, d_from.day) != (1, 1) or (d_to.month, d_to.day) != (12, 31):
        return None
    if d_from.year != d_to.year:
        return None
    return d_from.year


def from_transaction_filters(
    *,
    account_id: str,
    category: str,
    subcategory: str,
    txn_type: str,
    date_from: str,
    date_to: str,
) -> list[Crumb]:
    """Reconstruct a Reports breadcrumb trail from the transaction list's own filters.

    Purely derived from whichever filters are already in effect (including
    their sticky-cookie fallback, per ``list_transactions``) — there is no
    separate "came from a report link" side channel to maintain. A date
    range spanning exactly one calendar year or month reads as that
    period; a set category (paired with an income/expense ``txn_type`` —
    transfers have no category tree, see ``services.transactions
    .new_transfer_pair``) reads as that category's own ``/reports/category``
    page. A filter combination that doesn't match any recognizable report
    shape (a custom range from the toolbar's own date inputs, or no
    filters at all) yields an empty list, and the transactions page shows
    no breadcrumb bar — unchanged from before this existed.
    """
    month_span = _month_span(date_from, date_to) if date_from and date_to else None
    year_span = (
        None
        if month_span
        else (_year_span(date_from, date_to) if date_from and date_to else None)
    )
    has_category = bool(category) and txn_type in (
        TransactionType.INCOME.value,
        TransactionType.EXPENSE.value,
    )

    if not month_span and not year_span and not has_category:
        return []

    crumbs = [Crumb("Reports", _report_link("/reports", account_id))]
    if month_span:
        year, month = month_span
        crumbs.append(Crumb(str(year), _report_link(f"/reports/{year}", account_id)))
        crumbs.append(
            Crumb(
                calendar.month_name[month],
                _report_link(f"/reports/{year}/{month}", account_id),
            )
        )
    elif year_span:
        crumbs.append(
            Crumb(str(year_span), _report_link(f"/reports/{year_span}", account_id))
        )

    if has_category:
        cat_url = f"/reports/category?txn_type={txn_type}&category={quote(category)}"
        if account_id:
            cat_url += f"&account_id={quote(account_id)}"
        crumbs.append(Crumb(category, cat_url))
        if subcategory:
            crumbs.append(Crumb(subcategory, None))

    crumbs.append(Crumb("Transactions", None))
    return crumbs
