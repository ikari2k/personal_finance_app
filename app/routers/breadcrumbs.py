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
    search: str = "",
) -> str:
    """Build a ``/transactions`` URL that fully specifies every sticky filter.

    Always emits all seven of ``list_transactions``'s sticky-cookie
    filters explicitly (``account_id``/``category``/``subcategory``/
    ``txn_type``/``search``/``date_from``/``date_to``), even the ones
    left at their default ``""`` — an *omitted* param there falls back
    to whatever's in the visitor's sticky cookie (see
    ``list_transactions``'s own docstring), so a link built to mean
    "exactly this month, no category filter" has to say so explicitly
    or a stale category/search cookie from an earlier visit silently
    narrows what the link actually shows. Same "explicit empty
    overrides the sticky cookie" convention as the transactions
    toolbar's own Clear filters button.
    """
    params = {
        "date_from": date_from,
        "date_to": date_to,
        "category": category,
        "subcategory": subcategory,
        "txn_type": txn_type,
        "search": search,
        "account_id": account_id,
    }
    query = "&".join(f"{key}={quote(value)}" for key, value in params.items())
    return f"/transactions?{query}"


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


def _categories_link(txn_type: str, account_id: str) -> str:
    params = [f"txn_type={txn_type}"] if txn_type else []
    if account_id:
        params.append(f"account_id={quote(account_id)}")
    query = "&".join(params)
    return f"/reports/categories?{query}" if query else "/reports/categories"


def for_category(
    category: str,
    account_id: str = "",
    txn_type: str = "",
    year: int | None = None,
    month: int | None = None,
) -> list[Crumb]:
    """Breadcrumb trail for ``/reports/category``[``/{year}``[``/{month}``]].

    ``txn_type`` (when given) points the "Categories" crumb at
    ``/reports/categories`` already scoped to the right income/expense
    toggle, rather than always landing on its own default. Once scoped
    to a year (and/or month), the category's own crumb becomes a link
    back to the plain all-time view and a year crumb appears linking to
    the year view — the trail itself is then how you get back up a
    level, not just the page's own "View all-time stats" link.
    """
    crumbs = [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb("Categories", _categories_link(txn_type, account_id)),
    ]
    if year is None:
        crumbs.append(Crumb(category, None))
        return crumbs

    category_qs = f"txn_type={txn_type}&category={quote(category)}"
    if account_id:
        category_qs += f"&account_id={quote(account_id)}"
    crumbs.append(Crumb(category, f"/reports/category?{category_qs}"))
    if month is None:
        crumbs.append(Crumb(str(year), None))
        return crumbs

    crumbs.append(Crumb(str(year), f"/reports/category/{year}?{category_qs}"))
    crumbs.append(Crumb(calendar.month_name[month], None))
    return crumbs


def for_subcategory(
    category: str,
    subcategory: str,
    account_id: str = "",
    txn_type: str = "",
    year: int | None = None,
    month: int | None = None,
) -> list[Crumb]:
    """Breadcrumb trail for ``/reports/subcategory``[``/{year}``[``/{month}``]].

    Same "trail is itself the way back up a level" shape as
    ``for_category``, with one more rung: the category's own crumb
    always links back to its all-time ``/reports/category`` page (the
    subcategory page has no scope-less view of "the category itself"),
    then the subcategory, then year/month same as ``for_category``.
    """
    category_qs = f"txn_type={txn_type}&category={quote(category)}"
    if account_id:
        category_qs += f"&account_id={quote(account_id)}"
    crumbs = [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb("Categories", _categories_link(txn_type, account_id)),
        Crumb(category, f"/reports/category?{category_qs}"),
    ]
    subcategory_qs = category_qs + f"&subcategory={quote(subcategory)}"
    if year is None:
        crumbs.append(Crumb(subcategory, None))
        return crumbs

    crumbs.append(Crumb(subcategory, f"/reports/subcategory?{subcategory_qs}"))
    if month is None:
        crumbs.append(Crumb(str(year), None))
        return crumbs

    crumbs.append(Crumb(str(year), f"/reports/subcategory/{year}?{subcategory_qs}"))
    crumbs.append(Crumb(calendar.month_name[month], None))
    return crumbs


def for_categories_index(account_id: str = "") -> list[Crumb]:
    """Breadcrumb trail for ``/reports/categories``."""
    return [
        Crumb("Reports", _report_link("/reports", account_id)),
        Crumb("Categories", None),
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
        crumbs.append(Crumb("Categories", _categories_link(txn_type, account_id)))
        cat_url = f"/reports/category?txn_type={txn_type}&category={quote(category)}"
        if account_id:
            cat_url += f"&account_id={quote(account_id)}"
        crumbs.append(Crumb(category, cat_url))
        if subcategory:
            crumbs.append(Crumb(subcategory, None))

    crumbs.append(Crumb("Transactions", None))
    return crumbs
