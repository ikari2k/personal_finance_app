"""Business rules for recording transactions and transfers.

Pure functions over in-memory data — no file I/O. Callers (routers) read
the current ledger/accounts/categories via ``app.storage``, call these to
build and validate new rows, and write the result back afterward.
"""

import uuid
from collections.abc import Iterable
from datetime import date as date_
from decimal import Decimal

from app.models.category import CategoriesByType
from app.models.transaction import Transaction, TransactionType


def ensure_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
) -> CategoriesByType:
    """Return ``categories`` with ``category``/``subcategory`` added if new.

    Implements the PRD's "create categories on the fly" rule: entering a
    transaction against an unknown category or subcategory adds it rather
    than rejecting the transaction. A blank ``subcategory`` is not added.
    Income and expense have separate trees, keyed by ``txn_type.value``;
    ``txn_type`` must be ``INCOME`` or ``EXPENSE`` (transfers use a fixed
    category outside this tree — see ``new_transfer_pair``).
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers do not use the managed category tree")
    updated = {
        bucket: {name: list(subs) for name, subs in tree.items()}
        for bucket, tree in categories.items()
    }
    tree = updated.setdefault(txn_type.value, {})
    subcategories = tree.setdefault(category, [])
    if subcategory and subcategory not in subcategories:
        subcategories.append(subcategory)
    return updated


def new_transaction(
    account_ids: Iterable[str],
    *,
    account_id: str,
    date: date_,
    category: str,
    subcategory: str,
    description: str,
    amount: Decimal,
    type: TransactionType,
    notes: str | None = None,
) -> Transaction:
    """Build a new income/expense ``Transaction``.

    ``amount`` is the unsigned magnitude the user entered; it's normalized
    to a signed value here (expenses negative, income positive). Raises
    ``ValueError`` if ``account_id`` isn't a known account, ``amount``
    isn't positive, or ``type`` is ``TRANSFER`` (use ``new_transfer_pair``
    instead, since a transfer is two linked rows, not one).
    """
    if type is TransactionType.TRANSFER:
        raise ValueError("use new_transfer_pair() to record a transfer")
    if account_id not in set(account_ids):
        raise ValueError(f"unknown account_id '{account_id}'")
    if amount <= 0:
        raise ValueError("amount must be positive")

    signed_amount = -amount if type is TransactionType.EXPENSE else amount
    return Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=signed_amount,
        type=type,
        transfer_id=None,
        notes=notes,
    )


def new_transfer_pair(
    account_ids: Iterable[str],
    *,
    from_account_id: str,
    to_account_id: str,
    date: date_,
    amount: Decimal,
    category: str = "Transfer",
    subcategory: str = "",
    description: str = "",
    notes: str | None = None,
) -> tuple[Transaction, Transaction]:
    """Build the linked outflow/inflow row pair for a transfer.

    Raises ``ValueError`` if either account is unknown, the two accounts
    are the same, or ``amount`` isn't positive.
    """
    known_ids = set(account_ids)
    if from_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{from_account_id}'")
    if to_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{to_account_id}'")
    if from_account_id == to_account_id:
        raise ValueError("a transfer must be between two different accounts")
    if amount <= 0:
        raise ValueError("amount must be positive")

    transfer_id = uuid.uuid4().hex
    outflow = Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=from_account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=-amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    inflow = Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=to_account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    return outflow, inflow
