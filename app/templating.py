"""Shared Jinja2 templates instance, importable by main.py and every router.

Kept separate from ``app.main`` so routers can import it without creating a
circular import (``main`` imports and mounts every router).
"""

from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app import config

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
templates = Jinja2Templates(directory=APP_DIR / "templates")


def static_version(path: str) -> int:
    """Return a static asset's mtime, used as a cache-busting query param.

    Browsers otherwise keep serving a cached ``style.css``/``htmx.min.js``
    after an edit, since neither uvicorn's ``--reload`` nor a plain page
    reload is guaranteed to force a re-fetch — only the URL changing does.
    """
    return int((STATIC_DIR / path).stat().st_mtime)


templates.env.globals["static_version"] = static_version


def money(value: object) -> str:
    """Format an amount for display: thousands separators, two decimals.

    Accepts ``Decimal``/``float``/``int``/numeric ``str`` (budgets are
    stored as quoted strings). Display only — never use it for a form
    field's ``value``, which must stay a plain decimal the server can parse.
    """
    return f"{Decimal(str(value)):,.2f}"


templates.env.filters["money"] = money
templates.env.globals["currency"] = config.CURRENCY
