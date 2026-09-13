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
        bucket: {
            name: {
                "icon": entry["icon"],
                "budget": entry["budget"],
                "subcategories": dict(entry["subcategories"]),
            }
            for name, entry in tree.items()
        }
        for bucket, tree in categories.items()
    }
    tree = updated.setdefault(txn_type.value, {})
    entry = tree.setdefault(category, {"icon": "", "budget": "", "subcategories": {}})
    if subcategory and subcategory not in entry["subcategories"]:
        entry["subcategories"][subcategory] = {"icon": "", "budget": ""}
    return updated


def _signed_amount(
    account_ids: Iterable[str],
    *,
    account_id: str,
    amount: Decimal,
    type: TransactionType,
) -> Decimal:
    """Validate and sign-normalize an income/expense amount.

    Shared by ``new_transaction`` and ``update_transaction``. Raises
    ``ValueError`` if ``account_id`` isn't known, ``amount`` isn't
    positive, or ``type`` is ``TRANSFER`` (transfers never go through this
    path — see ``new_transfer_pair``).
    """
    if type is TransactionType.TRANSFER:
        raise ValueError("use new_transfer_pair() to record a transfer")
    if account_id not in set(account_ids):
        raise ValueError(f"unknown account_id '{account_id}'")
    if amount <= 0:
        raise ValueError("amount must be positive")
    return -amount if type is TransactionType.EXPENSE else amount


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
    signed_amount = _signed_amount(
        account_ids, account_id=account_id, amount=amount, type=type
    )
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


def update_transaction(
    transactions: list[Transaction],
    transaction_id: str,
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
) -> list[Transaction]:
    """Return ``transactions`` with ``transaction_id``'s row replaced.

    Same validation as ``new_transaction`` (unknown account, non-positive
    amount, ``TRANSFER`` type all raise ``ValueError``), but keeps the
    original ``id`` rather than minting a new one. Also raises
    ``ValueError`` if no transaction with ``transaction_id`` exists, or if
    it currently *is* a transfer leg — a transfer's two rows must change
    together or not at all, which this single-row function can't do; use
    ``update_transfer_pair`` instead.
    """
    existing = next((t for t in transactions if t.id == transaction_id), None)
    if existing is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    if existing.type is TransactionType.TRANSFER:
        raise ValueError(
            "transfers can't be edited via update_transaction; use update_transfer_pair"
        )

    signed_amount = _signed_amount(
        account_ids, account_id=account_id, amount=amount, type=type
    )
    updated = Transaction(
        id=transaction_id,
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
    return [updated if t.id == transaction_id else t for t in transactions]


def update_transfer_pair(
    transactions: list[Transaction],
    transfer_id: str,
    account_ids: Iterable[str],
    *,
    from_account_id: str,
    to_account_id: str,
    date: date_,
    amount: Decimal,
    description: str = "",
    notes: str | None = None,
) -> list[Transaction]:
    """Return ``transactions`` with both legs of ``transfer_id`` replaced.

    The single-row ``update_transaction`` refuses to touch a transfer leg
    (see its docstring) because a transfer is two linked rows that must
    change together or not at all — this is that atomic path: both legs
    are rebuilt from the new field values and swapped in together, never
    just one. Same validation as ``new_transfer_pair`` (unknown account,
    same account on both sides, non-positive amount all raise
    ``ValueError``), but each leg keeps its original ``id`` rather than
    minting new ones. Category/subcategory are carried over from the
    existing legs unchanged (transfers always use the fixed "Transfer"
    category — the edit form doesn't expose it). Raises ``ValueError`` if
    ``transfer_id`` doesn't identify exactly one outflow and one inflow
    row.
    """
    legs = [t for t in transactions if t.transfer_id == transfer_id]
    outflow = next((t for t in legs if t.amount < 0), None)
    inflow = next((t for t in legs if t.amount > 0), None)
    if len(legs) != 2 or outflow is None or inflow is None:
        raise ValueError(f"no transfer with id '{transfer_id}'")

    known_ids = set(account_ids)
    if from_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{from_account_id}'")
    if to_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{to_account_id}'")
    if from_account_id == to_account_id:
        raise ValueError("a transfer must be between two different accounts")
    if amount <= 0:
        raise ValueError("amount must be positive")

    updated_outflow = Transaction(
        id=outflow.id,
        date=date,
        account_id=from_account_id,
        category=outflow.category,
        subcategory=outflow.subcategory,
        description=description,
        amount=-amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    updated_inflow = Transaction(
        id=inflow.id,
        date=date,
        account_id=to_account_id,
        category=inflow.category,
        subcategory=inflow.subcategory,
        description=description,
        amount=amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    replacements = {outflow.id: updated_outflow, inflow.id: updated_inflow}
    return [replacements.get(t.id, t) for t in transactions]


def remove_transaction(
    transactions: list[Transaction], transaction_id: str
) -> list[Transaction]:
    """Return ``transactions`` with ``transaction_id`` removed.

    A transfer is two linked rows sharing a ``transfer_id`` (see
    ``new_transfer_pair``); deleting only one leg would either leave the
    other orphaned or desync the pair's amounts — exactly what
    ``services.consistency.check_consistency`` flags. So deleting a
    transfer leg removes *both* legs together in this one call; deleting
    a plain income/expense row removes just that row. Raises
    ``ValueError`` if no transaction with ``transaction_id`` exists.
    """
    target = next((t for t in transactions if t.id == transaction_id), None)
    if target is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    if target.transfer_id is not None:
        return [t for t in transactions if t.transfer_id != target.transfer_id]
    return [t for t in transactions if t.id != transaction_id]


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
