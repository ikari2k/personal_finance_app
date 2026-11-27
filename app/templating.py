"""Shared Jinja2 templates instance, importable by main.py and every router.

Kept separate from ``app.main`` so routers can import it without creating a
circular import (``main`` imports and mounts every router).
"""

from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app import config
from app.storage.payee_logos import logo_url
from app.storage.payees import read_payee_book

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
templates.env.globals["payee_logo_url"] = logo_url

PERSON_LOGO_URL = "/static/img/person.png"
_people_cache: tuple[Path, int, frozenset[str]] | None = None


def _people() -> frozenset[str]:
    """Return the casefolded names marked as a person, re-read when the file changes.

    A page can ask for hundreds of payee images, so the payee book is parsed only
    when its modification time moves, not once per row.
    """
    global _people_cache
    path = config.PAYEE_ALIASES_PATH
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = -1
    if _people_cache is None or _people_cache[:2] != (path, stamp):
        names = frozenset(n.casefold() for n in read_payee_book(path).people)
        _people_cache = (path, stamp, names)
    return _people_cache[2]


def payee_image_url(name: str) -> str:
    """Return the image shown for payee ``name``.

    Its own logo if it has one, else the default picture for a person, else ``""``.
    """
    url = logo_url(name)
    if url:
        return url
    return PERSON_LOGO_URL if name and name.casefold() in _people() else ""


templates.env.globals["payee_image_url"] = payee_image_url
templates.env.globals["payee_person_logo_url"] = PERSON_LOGO_URL


def money(value: object) -> str:
    """Format an amount for display: thousands separators, two decimals.

    Accepts ``Decimal``/``float``/``int``/numeric ``str`` (budgets are
    stored as quoted strings). Display only — never use it for a form
    field's ``value``, which must stay a plain decimal the server can parse.
    """
    return f"{Decimal(str(value)):,.2f}"


templates.env.filters["money"] = money
templates.env.globals["currency"] = config.CURRENCY
