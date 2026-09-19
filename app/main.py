"""FastAPI application entry point.

Wires up the app instance, static/template mounting, a non-blocking ledger
consistency check on startup, feature routers, and a health-check page
rendered through Jinja2.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    accounts,
    categories,
    import_,
    reports,
    rules,
    transactions,
    transfer_detection,
    transfers,
)
from app.services.consistency import check_consistency
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger
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
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(transactions.router)
app.include_router(transfers.router)
app.include_router(import_.router)
app.include_router(transfer_detection.router)
app.include_router(rules.router)
app.include_router(reports.router)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    """Redirect the root path to the accounts page."""
    return RedirectResponse(url="/accounts")


@app.get("/health", response_class=HTMLResponse)
def health_check(request: Request) -> HTMLResponse:
    """Render a minimal page confirming the server is running."""
    return templates.TemplateResponse(request, "health.html", {})
