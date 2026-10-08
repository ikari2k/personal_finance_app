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
