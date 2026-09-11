"""Shared Jinja2 templates instance, importable by main.py and every router.

Kept separate from ``app.main`` so routers can import it without creating a
circular import (``main`` imports and mounts every router).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")
