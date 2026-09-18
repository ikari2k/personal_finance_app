"""Read/write access to ``data/ledger.csv``.

The full ledger is loaded into memory and rewritten in full on every write
— acceptable at the project's assumed scale (~7,000-9,000 rows; see
CLAUDE.md). Writes are lock-guarded and go through a temp file + atomic
rename so a crash mid-write can never leave a truncated ledger on disk.
"""

import csv
from collections.abc import Iterable
from pathlib import Path

from app import config
from app.models.transaction import Transaction
from app.storage.lock import file_lock

LEDGER_FIELDNAMES = [
    "id",
    "date",
    "account_id",
    "category",
    "subcategory",
    "description",
    "amount",
    "type",
    "transfer_id",
    "notes",
    "counterparty_account",
]


def _to_row(transaction: Transaction) -> dict[str, str]:
    """Convert a ``Transaction`` into a CSV row of strings."""
    return {
        "id": transaction.id,
        "date": transaction.date.isoformat(),
        "account_id": transaction.account_id,
        "category": transaction.category,
        "subcategory": transaction.subcategory,
        "description": transaction.description,
        "amount": str(transaction.amount),
        "type": transaction.type.value,
        "transfer_id": transaction.transfer_id or "",
        "notes": transaction.notes or "",
        "counterparty_account": transaction.counterparty_account,
    }


def _from_row(row: dict[str, str]) -> Transaction:
    """Parse a CSV row (as produced by ``csv.DictReader``) into a ``Transaction``.

    ``counterparty_account`` uses ``.get`` with a blank default so a
    ledger written before this column existed still reads back fine — a
    missing column means "not captured", same as an explicit blank cell
    (same tolerance already established for ``config/rules.toml``'s
    amount/type bounds).
    """
    return Transaction(
        id=row["id"],
        date=row["date"],
        account_id=row["account_id"],
        category=row["category"],
        subcategory=row["subcategory"],
        description=row["description"],
        amount=row["amount"],
        type=row["type"],
        transfer_id=row["transfer_id"] or None,
        notes=row["notes"] or None,
        counterparty_account=row.get("counterparty_account") or "",
    )


def read_ledger(path: Path | None = None) -> list[Transaction]:
    """Read every transaction from the ledger CSV at ``path``.

    Defaults to ``app.config.LEDGER_PATH``, resolved at call time (not at
    import time) so tests can redirect it via ``monkeypatch``. Returns an
    empty list if the file does not exist yet — the ledger is created
    lazily on first write, not at app startup.
    """
    path = path if path is not None else config.LEDGER_PATH
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as ledger_file:
        reader = csv.DictReader(ledger_file)
        return [_from_row(row) for row in reader]


def write_ledger(transactions: Iterable[Transaction], path: Path | None = None) -> None:
    """Overwrite the ledger CSV at ``path`` with ``transactions``.

    Defaults to ``app.config.LEDGER_PATH``, resolved at call time. Acquires
    a PID-based lock on ``path`` for the duration of the write and writes
    via a temp file + atomic rename.
    """
    path = path if path is not None else config.LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8") as tmp_file:
            writer = csv.DictWriter(tmp_file, fieldnames=LEDGER_FIELDNAMES)
            writer.writeheader()
            for transaction in transactions:
                writer.writerow(_to_row(transaction))
        tmp_path.replace(path)
