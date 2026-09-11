"""FastAPI application entry point.

Phase 0 wires up the app instance, static/template mounting, and a
health-check page rendered through Jinja2 to prove out the templating
pipeline later phases build on. Feature routers (accounts, transactions,
imports, rules, reports) are mounted here as each phase lands.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

app = FastAPI(title="Personal Finance Tracker")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/health", response_class=HTMLResponse)
def health_check(request: Request) -> HTMLResponse:
    """Render a minimal page confirming the server is running."""
    return templates.TemplateResponse(request, "health.html", {})
