"""Read/write access to ``config/categories.toml``.

Two separate category → subcategory trees are stored, under top-level
``[income]`` and ``[expense]`` tables — see ``app.models.category`` for why
they're kept apart. No dedup or cleanup is performed here — keeping either
tree tidy is a user responsibility per the PRD.
"""

import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.models.category import CategoriesByType, CategoryEntry
from app.storage.lock import file_lock


def _normalize_entry(raw: list[str] | dict[str, object]) -> CategoryEntry:
    """Coerce one category's raw TOML value into the current shape.

    Pre-icon-feature files store a category as a bare list of subcategory
    names (``Salary = []``, ``Groceries = ["Supermarket"]``); this upgrades
    that into ``{"icon": "", "subcategories": {name: ""}}`` in memory so
    old files keep loading without a separate migration step. The next
    ``write_categories`` call persists the upgraded shape.
    """
    if isinstance(raw, list):
        return {"icon": "", "subcategories": {name: "" for name in raw}}
    icon = raw.get("icon", "")
    subcategories = raw.get("subcategories", {})
    return {
        "icon": icon if isinstance(icon, str) else "",
        "subcategories": dict(subcategories) if isinstance(subcategories, dict) else {},
    }


def read_categories(path: Path | None = None) -> CategoriesByType:
    """Read the income/expense category trees from the TOML file at ``path``.

    Defaults to ``app.config.CATEGORIES_PATH``, resolved at call time (not
    at import time) so tests can redirect it via ``monkeypatch``. Returns
    two empty trees if the file does not exist yet.
    """
    path = path if path is not None else config.CATEGORIES_PATH
    if not path.exists():
        return {"income": {}, "expense": {}}
    with path.open("rb") as categories_file:
        data = tomllib.load(categories_file)
    return {
        "income": {
            name: _normalize_entry(entry)
            for name, entry in data.get("income", {}).items()
        },
        "expense": {
            name: _normalize_entry(entry)
            for name, entry in data.get("expense", {}).items()
        },
    }


def write_categories(categories: CategoriesByType, path: Path | None = None) -> None:
    """Overwrite the categories TOML file at ``path`` with ``categories``.

    Defaults to ``app.config.CATEGORIES_PATH``, resolved at call time.
    Acquires a PID-based lock on ``path`` for the duration of the write and
    writes via a temp file + atomic rename.
    """
    path = path if path is not None else config.CATEGORIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {
            "income": categories.get("income", {}),
            "expense": categories.get("expense", {}),
        }
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
