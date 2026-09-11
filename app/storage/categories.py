"""Read/write access to ``config/categories.toml``.

The category tree is a flat mapping of category name → list of subcategory
names, stored under a single ``[categories]`` table. No dedup or cleanup is
performed here — keeping the tree tidy is a user responsibility per the PRD.
"""

import tomllib
from pathlib import Path

import tomli_w

from app.config import CATEGORIES_PATH
from app.models.category import CategoryTree
from app.storage.lock import file_lock


def read_categories(path: Path = CATEGORIES_PATH) -> CategoryTree:
    """Read the category tree from the TOML file at ``path``.

    Returns an empty tree if the file does not exist yet.
    """
    if not path.exists():
        return {}
    with path.open("rb") as categories_file:
        data = tomllib.load(categories_file)
    return data.get("categories", {})


def write_categories(categories: CategoryTree, path: Path = CATEGORIES_PATH) -> None:
    """Overwrite the categories TOML file at ``path`` with ``categories``.

    Acquires a PID-based lock on ``path`` for the duration of the write and
    writes via a temp file + atomic rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {"categories": categories}
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
