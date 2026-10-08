"""FastAPI application entry point.

Wires up the app instance, static/template mounting, a non-blocking ledger
consistency check on startup, feature routers, and a health-check page
rendered through Jinja2.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.routers import (
    accounts,
    budget_rule,
    categories,
    dashboard,
    import_,
    reports,
    rules,
    transactions,
    transfer_detection,
    transfers,
)
from app.routers.htmx_events import toast
from app.routers.scope import ACCOUNT_COOKIE, COOKIE_MAX_AGE
from app.services.consistency import check_consistency
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger
from app.storage.lock import LockError
from app.templating import APP_DIR, templates

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the ledger consistency check at startup without blocking it.

    Issues are logged as warnings; a dirty ledger never prevents the app
    from starting. A matching on-demand check is added in a later phase.
    """
    issues = check_consistency(read_ledger(), read_accounts())
    if issues:
        for issue in issues:
            logger.warning(
                "Ledger consistency issue [%s] txn=%s: %s",
                issue.kind,
                issue.transaction_id,
                issue.detail,
            )
    else:
        logger.info("Ledger consistency check: no issues found.")
    yield


app = FastAPI(title="Personal Finance Tracker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(transactions.router)
app.include_router(transfers.router)
app.include_router(import_.router)
app.include_router(transfer_detection.router)
app.include_router(rules.router)
app.include_router(reports.router)
app.include_router(budget_rule.router)


@app.middleware("http")
async def remember_account_scope(request: Request, call_next) -> Response:
    """Remember an account chosen on Reports/Dashboard in the shared cookie.

    Transactions writes the same ``account_id`` cookie itself; this makes the
    other pages do likewise whenever the URL carries ``account_id`` (an
    explicit empty value means "all accounts" and is remembered too), so one
    choice follows the user between pages.
    """
    response = await call_next(request)
    if (
        request.method == "GET"
        and request.url.path.startswith(("/reports", "/dashboard"))
        and "account_id" in request.query_params
    ):
        response.set_cookie(
            ACCOUNT_COOKIE,
            request.query_params["account_id"],
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
        )
    return response


@app.exception_handler(LockError)
async def handle_lock_error(request: Request, exc: LockError) -> Response:
    """Turn a file-lock timeout into a toast instead of a raw 500.

    Every write goes through `app.storage.lock.file_lock`; a conflict here
    means another save was already in flight when this request's own write
    tried to acquire the same lock — rare for a single-user app but possible
    with two browser tabs open at once. The request's original hx-target
    could be a dialog, a table fragment, or a single row, so the response
    leaves the DOM untouched (`HX-Reswap: none`) rather than risk replacing
    whatever was there with an error message — the toast just tells the user
    to retry.
    """
    logger.warning("Lock conflict on write: %s", exc)
    headers = toast("Could not save — another save was in progress. Please try again.")
    headers["HX-Reswap"] = "none"
    return Response(status_code=200, headers=headers)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    """Redirect the root path to the dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/health", response_class=HTMLResponse)
def health_check(request: Request) -> HTMLResponse:
    """Render a minimal page confirming the server is running."""
    return templates.TemplateResponse(request, "health.html", {})
