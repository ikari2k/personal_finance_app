"""Read/write access to ``data/import_history.toml``.

An append-only activity log (see ``app.models.import_history``), not
configuration — lives under ``data/`` alongside the ledger rather than
``config/`` for that reason. Follows the same storage-module shape as
every other module here regardless.
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path

import tomli_w

from app import config
from app.models.import_history import ImportHistoryEntry
from app.storage.lock import file_lock


def read_history(path: Path | None = None) -> list[ImportHistoryEntry]:
    """Read every past import entry from the TOML file at ``path``.

    Defaults to ``app.config.IMPORT_HISTORY_PATH``, resolved at call time
    (not at import time) so tests can redirect it via ``monkeypatch``.
    Returns an empty list if the file does not exist yet. Sorted newest
    first — that's the order every view of this data wants.
    """
    path = path if path is not None else config.IMPORT_HISTORY_PATH
    if not path.exists():
        return []
    with path.open("rb") as history_file:
        data = tomllib.load(history_file)
    entries = [ImportHistoryEntry(**entry) for entry in data.get("imports", [])]
    return sorted(entries, key=lambda entry: entry.timestamp, reverse=True)


def write_history(
    entries: Iterable[ImportHistoryEntry], path: Path | None = None
) -> None:
    """Overwrite the import history TOML file at ``path`` with ``entries``.

    Defaults to ``app.config.IMPORT_HISTORY_PATH``, resolved at call
    time. Acquires a PID-based lock on ``path`` for the duration of the
    write and writes via a temp file + atomic rename, same as every
    other ``storage`` module. ``timestamp`` is stored as an ISO 8601
    string rather than relying on TOML's native datetime type, matching
    this app's existing convention of plain-string dates elsewhere (the
    ledger CSV's own date column) over an exotic-format round-trip.
    """
    path = path if path is not None else config.IMPORT_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {
            "imports": [
                {**entry.model_dump(), "timestamp": entry.timestamp.isoformat()}
                for entry in entries
            ]
        }
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)


def append_history_entry(entry: ImportHistoryEntry, path: Path | None = None) -> None:
    """Append one entry to the import history TOML file at ``path``.

    A convenience wrapper around ``read_history``/``write_history`` for
    the one caller (``routers.import_.confirm``) that only ever adds a
    single new entry per request.
    """
    path = path if path is not None else config.IMPORT_HISTORY_PATH
    write_history([*read_history(path), entry], path)
