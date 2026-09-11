"""FastAPI application entry point.

Wires up the app instance, static/template mounting, a non-blocking ledger
consistency check on startup, and a health-check page rendered through
Jinja2. Feature routers (accounts, transactions, imports, rules, reports)
are mounted here as each phase lands.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.consistency import check_consistency
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


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


@app.get("/health", response_class=HTMLResponse)
def health_check(request: Request) -> HTMLResponse:
    """Render a minimal page confirming the server is running."""
    return templates.TemplateResponse(request, "health.html", {})
