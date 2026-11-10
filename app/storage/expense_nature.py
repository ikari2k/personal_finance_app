"""Read/write access to ``config/expense_nature.toml``.

Holds the user's fixed/semi/flexible tag per expense category or
subcategory as an array of ``[[tags]]`` tables (``category``,
``subcategory``, ``nature``). Kept out of ``categories.toml`` on purpose:
that file is rewritten on every transaction entry and import, and a field
added to its entries has been silently dropped by a rebuild before. A
subcategory of ``""`` tags the whole category.
"""

import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.services.recurring import NATURES
from app.storage.backup import backup_before_write
from app.storage.lock import file_lock

NatureTags = dict[tuple[str, str], str]


def read_nature_tags(path: Path | None = None) -> NatureTags:
    """Read ``(category, subcategory) -> nature`` from the TOML file at ``path``.

    Defaults to ``app.config.EXPENSE_NATURE_PATH``, resolved at call time.
    Returns an empty mapping if the file is missing; rows with an unknown
    nature are skipped.
    """
    path = path if path is not None else config.EXPENSE_NATURE_PATH
    if not path.exists():
        return {}
    with path.open("rb") as tags_file:
        data = tomllib.load(tags_file)
    return {
        (row["category"], row.get("subcategory", "")): row["nature"]
        for row in data.get("tags", [])
        if row.get("nature") in NATURES and row.get("category")
    }


def write_nature_tags(tags: NatureTags, path: Path | None = None) -> None:
    """Overwrite the TOML file at ``path`` with ``tags``.

    Defaults to ``app.config.EXPENSE_NATURE_PATH``. Locks ``path``, leaves
    the usual config backups, and replaces the file atomically.
    """
    path = path if path is not None else config.EXPENSE_NATURE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        backup_before_write(path)
        rows = [
            {"category": category, "subcategory": subcategory, "nature": nature}
            for (category, subcategory), nature in sorted(tags.items())
        ]
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump({"tags": rows}, tmp_file)
        tmp_path.replace(path)
