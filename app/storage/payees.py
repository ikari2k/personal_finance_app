"""Read/write access to ``config/payee_aliases.toml``.

Holds the payee book: ``[[rules]]`` tables (a ``payee`` plus a ``[rules.fields]``
table of label -> exact value), a ``skipped`` list of group keys left unnamed on
purpose and a ``split`` list of group keys divided into single spellings.
Written only by the payee management view; config writes leave
backups like the other config files.

The file's first format held ``[[aliases]]`` (``spelling``, ``payee``). Those
are still read, as rules on the ``Description`` field alone, and are written
back in the rule format the next time the book is saved.
"""

import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.models.payee import DESCRIPTION_FIELD, PayeeBook, PayeeRule
from app.storage.backup import backup_before_write
from app.storage.lock import file_lock


def read_payee_book(path: Path | None = None) -> PayeeBook:
    """Read the payee book from the TOML file at ``path``.

    Defaults to ``app.config.PAYEE_ALIASES_PATH``, resolved at call time.
    Returns an empty book if the file is missing. Legacy ``[[aliases]]`` come
    first (they are the oldest), then ``[[rules]]``; rows without a payee or
    any field value are skipped.
    """
    path = path if path is not None else config.PAYEE_ALIASES_PATH
    if not path.exists():
        return PayeeBook()
    with path.open("rb") as book_file:
        data = tomllib.load(book_file)
    rules = [
        PayeeRule(fields={DESCRIPTION_FIELD: row["spelling"]}, payee=row["payee"])
        for row in data.get("aliases", [])
        if row.get("spelling") and row.get("payee")
    ]
    for row in data.get("rules", []):
        fields = {
            str(label): str(value)
            for label, value in (row.get("fields") or {}).items()
            if str(value).strip()
        }
        if fields and row.get("payee"):
            rules.append(PayeeRule(fields=fields, payee=row["payee"]))
    return PayeeBook(
        rules=rules,
        skipped=list(data.get("skipped", [])),
        split=list(data.get("split", [])),
    )


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
            "split": list(book.split),
            "rules": [
                {"payee": rule.payee, "fields": dict(rule.fields)}
                for rule in book.rules
            ],
        }
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
