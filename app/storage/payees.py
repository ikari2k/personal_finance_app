"""Read/write access to ``config/payee_aliases.toml``.

Holds the payee book: ``[[aliases]]`` tables (``spelling``, ``payee``) for the
bank spellings the user named, plus a ``skipped`` list of group keys left
unnamed on purpose. Written only by the payee management view; config writes
leave backups like the other config files.
"""

import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.models.payee import PayeeAlias, PayeeBook
from app.storage.backup import backup_before_write
from app.storage.lock import file_lock


def read_payee_book(path: Path | None = None) -> PayeeBook:
    """Read the payee book from the TOML file at ``path``.

    Defaults to ``app.config.PAYEE_ALIASES_PATH``, resolved at call time.
    Returns an empty book if the file is missing; rows missing a spelling or
    payee are skipped.
    """
    path = path if path is not None else config.PAYEE_ALIASES_PATH
    if not path.exists():
        return PayeeBook()
    with path.open("rb") as book_file:
        data = tomllib.load(book_file)
    aliases = [
        PayeeAlias(spelling=row["spelling"], payee=row["payee"])
        for row in data.get("aliases", [])
        if row.get("spelling") and row.get("payee")
    ]
    return PayeeBook(aliases=aliases, skipped=list(data.get("skipped", [])))


def write_payee_book(book: PayeeBook, path: Path | None = None) -> None:
    """Overwrite the payee book TOML file at ``path`` with ``book``.

    Defaults to ``app.config.PAYEE_ALIASES_PATH``, resolved at call time.
    Locked, backed up, and written via a temp file + atomic rename.
    """
    path = path if path is not None else config.PAYEE_ALIASES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        backup_before_write(path)
        data = {
            "skipped": list(book.skipped),
            "aliases": [alias.model_dump() for alias in book.aliases],
        }
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
