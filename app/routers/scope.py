"""Shared "scope" state — the account (and month) the user is looking at.

The account choice is remembered in the same sticky ``account_id`` cookie the
transactions list already used, so picking an account on Reports carries over
to Transactions and the Dashboard, and vice versa. Routes treat an absent
query param as "use the remembered value" and an explicit empty string as
"all accounts" — the same rule ``list_transactions`` follows for its filters.
"""

import calendar
from datetime import date

from fastapi import Request

ACCOUNT_COOKIE = "account_id"
MONTH_COOKIE = "scope_month"
MONTH_APPLIED_COOKIE = "scope_month_applied"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def resolve_account(request: Request, account_id: str | None) -> str:
    """Return ``account_id`` if the URL set it (even to ""), else the cookie."""
    if account_id is not None:
        return account_id
    return request.cookies.get(ACCOUNT_COOKIE, "")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the first and last day of ``year``-``month``."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def parse_month_key(raw: str | None) -> tuple[int, int] | None:
    """Parse ``YYYY-MM`` into ``(year, month)``; ``None`` if blank or invalid."""
    if not raw or len(raw) != 7 or raw[4] != "-":
        return None
    try:
        year, month = int(raw[:4]), int(raw[5:])
        date(year, month, 1)
    except ValueError:
        return None
    return year, month


def month_key(year: int, month: int) -> str:
    """Format ``year``/``month`` as the ``YYYY-MM`` key the cookies and URLs use."""
    return f"{year:04d}-{month:02d}"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return the (year, month) ``delta`` months from ``year``-``month``."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def single_month_of(date_from: str, date_to: str) -> tuple[int, int] | None:
    """Return the month a date range covers exactly, else ``None``.

    "Exactly" means ``date_from`` is the 1st and ``date_to`` the last day of
    one calendar month — the range the scope bar's month control produces.
    """
    try:
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        return None
    if month_bounds(start.year, start.month) == (start, end):
        return start.year, start.month
    return None


def transactions_period(date_from: str, date_to: str, today: date) -> dict:
    """Build the scope bar's month control for the transactions list.

    Reflects the list's *actual* date range: a month stepper when the range
    is exactly one month, otherwise just an empty month picker (arrows
    disabled) so the user can jump into month mode from "all dates" or a
    custom range.
    """
    current = month_key(today.year, today.month)
    picker = {
        "key": "",
        "max": current,
        "action": "/transactions",
        "name": "scope_month",
    }
    month = single_month_of(date_from, date_to)
    if month is None:
        return {"prev_url": None, "next_url": None, "picker": picker}
    prev_year, prev_month = shift_month(*month, -1)
    next_year, next_month = shift_month(*month, 1)
    key = month_key(*month)
    picker["key"] = key
    return {
        "prev_url": f"/transactions?scope_month={month_key(prev_year, prev_month)}",
        "prev_label": month_key(prev_year, prev_month),
        "next_url": (
            f"/transactions?scope_month={month_key(next_year, next_month)}"
            if month_key(next_year, next_month) <= current
            else None
        ),
        "next_label": month_key(next_year, next_month),
        "picker": picker,
    }
