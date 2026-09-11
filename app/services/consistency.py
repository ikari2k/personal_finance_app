"""Ledger consistency checks.

There is no database to enforce referential integrity, so it's enforced
here: every transaction's ``account_id`` must name a known account, and
every transfer-type transaction must be one half of an exactly-two-row,
zero-net pair sharing a ``transfer_id``. Pure business logic — no file I/O
— so it can be unit-tested without touching disk.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType


@dataclass
class ConsistencyIssue:
    """A single problem found while checking the ledger for consistency."""

    kind: str
    transaction_id: str
    detail: str


def check_consistency(
    transactions: Iterable[Transaction], accounts: Iterable[Account]
) -> list[ConsistencyIssue]:
    """Check ``transactions`` against ``accounts`` and return any issues found.

    Flags transactions referencing an unknown ``account_id``
    (``kind="invalid_account"``) and transfer-type transactions that aren't
    part of a valid, zero-net two-row pair (``kind="orphaned_transfer"``).
    """
    issues: list[ConsistencyIssue] = []
    account_ids = {account.id for account in accounts}
    transfer_groups: dict[str, list[Transaction]] = defaultdict(list)

    for transaction in transactions:
        if transaction.account_id not in account_ids:
            issues.append(
                ConsistencyIssue(
                    kind="invalid_account",
                    transaction_id=transaction.id,
                    detail=f"references unknown account_id '{transaction.account_id}'",
                )
            )
        if transaction.type == TransactionType.TRANSFER:
            if transaction.transfer_id is None:
                issues.append(
                    ConsistencyIssue(
                        kind="orphaned_transfer",
                        transaction_id=transaction.id,
                        detail="transfer-type row has no transfer_id",
                    )
                )
            else:
                transfer_groups[transaction.transfer_id].append(transaction)

    for transfer_id, group in transfer_groups.items():
        if len(group) != 2:
            issues.append(
                ConsistencyIssue(
                    kind="orphaned_transfer",
                    transaction_id=group[0].id,
                    detail=(
                        f"transfer_id '{transfer_id}' has {len(group)} "
                        "row(s), expected 2"
                    ),
                )
            )
            continue
        first, second = group
        if first.amount + second.amount != 0:
            issues.append(
                ConsistencyIssue(
                    kind="orphaned_transfer",
                    transaction_id=first.id,
                    detail=f"transfer_id '{transfer_id}' amounts do not net to zero",
                )
            )

    return issues
