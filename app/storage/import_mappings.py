"""Read/write access to ``config/import_mappings/<bank>.toml``.

Unlike every other ``storage`` module, there's one file *per bank*
rather than a single fixed path — captured once via the mapping-setup UI
the first time a bank's CSV is imported, then reused automatically on
every later import from that bank (see ``app.models.import_mapping``).
"""

import re
import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.models.import_mapping import ImportMapping
from app.storage.lock import file_lock

_UNSAFE_FILENAME_CHARS = re.compile(r"[^a-z0-9]+")


def _filename(bank: str) -> str:
    """Turn a user-provided bank name into a safe ``<slug>.toml`` filename.

    Slugified (lowercased, non-alphanumeric runs collapsed to a single
    ``_``) so free-text bank names can't traverse or collide with
    filesystem-unsafe characters. The mapping's own ``bank`` field, not
    the filename, is the source of truth for display purposes.
    """
    slug = _UNSAFE_FILENAME_CHARS.sub("_", bank.strip().lower()).strip("_")
    if not slug:
        raise ValueError("bank name is required")
    return f"{slug}.toml"


def read_mapping(bank: str, dir_path: Path | None = None) -> ImportMapping | None:
    """Read the saved mapping for ``bank``, or ``None`` if it has none yet.

    Defaults to ``app.config.IMPORT_MAPPINGS_DIR``, resolved at call time
    (not at import time) so tests can redirect it via ``monkeypatch``.
    """
    dir_path = dir_path if dir_path is not None else config.IMPORT_MAPPINGS_DIR
    path = dir_path / _filename(bank)
    if not path.exists():
        return None
    with path.open("rb") as mapping_file:
        data = tomllib.load(mapping_file)
    return ImportMapping(**data)


def write_mapping(mapping: ImportMapping, dir_path: Path | None = None) -> None:
    """Save ``mapping``, overwriting any existing mapping for the same bank.

    Defaults to ``app.config.IMPORT_MAPPINGS_DIR``, resolved at call time.
    Acquires a PID-based lock on the target file for the duration of the
    write and writes via a temp file + atomic rename.
    """
    dir_path = dir_path if dir_path is not None else config.IMPORT_MAPPINGS_DIR
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / _filename(mapping.bank)
    with file_lock(path):
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(mapping.model_dump(), tmp_file)
        tmp_path.replace(path)


def list_banks(dir_path: Path | None = None) -> list[str]:
    """Return the display name of every bank with a saved mapping.

    Sorted for a stable order in the import UI's bank picker. Returns an
    empty list if the directory doesn't exist yet (no bank imported yet).
    """
    dir_path = dir_path if dir_path is not None else config.IMPORT_MAPPINGS_DIR
    if not dir_path.exists():
        return []
    banks = []
    for path in dir_path.glob("*.toml"):
        with path.open("rb") as mapping_file:
            data = tomllib.load(mapping_file)
        banks.append(data["bank"])
    return sorted(banks)
