"""Read/write access to ``config/rules.toml``.

Shared between import-time auto-categorization (``services.categorizer``)
and the Phase 4 bulk-reclassification rule-management UI (not built yet).
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path

import tomli_w

from app import config
from app.models.rule import Rule
from app.storage.lock import file_lock


def read_rules(path: Path | None = None) -> list[Rule]:
    """Read every rule from the TOML file at ``path``.

    Defaults to ``app.config.RULES_PATH``, resolved at call time (not at
    import time) so tests can redirect it via ``monkeypatch``. Returns an
    empty list if the file does not exist yet.
    """
    path = path if path is not None else config.RULES_PATH
    if not path.exists():
        return []
    with path.open("rb") as rules_file:
        data = tomllib.load(rules_file)
    return [Rule(**entry) for entry in data.get("rules", [])]


def write_rules(rules: Iterable[Rule], path: Path | None = None) -> None:
    """Overwrite the rules TOML file at ``path`` with ``rules``.

    Defaults to ``app.config.RULES_PATH``, resolved at call time. Acquires
    a PID-based lock on ``path`` for the duration of the write and writes
    via a temp file + atomic rename.
    """
    path = path if path is not None else config.RULES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {"rules": [rule.model_dump() for rule in rules]}
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
