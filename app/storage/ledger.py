"""Read/write access to ``data/ledger.csv``.

The full ledger is loaded into memory and rewritten in full on every write
— acceptable at the project's assumed scale (~7,000-9,000 rows; see
CLAUDE.md). Writes are lock-guarded and go through a temp file + atomic
rename so a crash mid-write can never leave a truncated ledger on disk.
"""

import csv
from collections.abc import Iterable
from pathlib import Path

from app.config import LEDGER_PATH
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
    }


def _from_row(row: dict[str, str]) -> Transaction:
    """Parse a CSV row (as produced by ``csv.DictReader``) into a ``Transaction``."""
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
    )


def read_ledger(path: Path = LEDGER_PATH) -> list[Transaction]:
    """Read every transaction from the ledger CSV at ``path``.

    Returns an empty list if the file does not exist yet — the ledger is
    created lazily on first write, not at app startup.
    """
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as ledger_file:
        reader = csv.DictReader(ledger_file)
        return [_from_row(row) for row in reader]


def write_ledger(transactions: Iterable[Transaction], path: Path = LEDGER_PATH) -> None:
    """Overwrite the ledger CSV at ``path`` with ``transactions``.

    Acquires a PID-based lock on ``path`` for the duration of the write and
    writes via a temp file + atomic rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8") as tmp_file:
            writer = csv.DictWriter(tmp_file, fieldnames=LEDGER_FIELDNAMES)
            writer.writeheader()
            for transaction in transactions:
                writer.writerow(_to_row(transaction))
        tmp_path.replace(path)
