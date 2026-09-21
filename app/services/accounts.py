"""Business rules for creating, editing, and deleting accounts.

Pure functions over in-memory ``Account`` lists — no file I/O. Callers
(routers) read the current accounts via ``app.storage.accounts`` before
calling these, and write the result back afterward.
"""

from decimal import Decimal

from app.models.account import Account, AccountStatus, AccountType
from app.models.transaction import Transaction


def add_account(accounts: list[Account], new_account: Account) -> list[Account]:
    """Return ``accounts`` with ``new_account`` appended.

    Raises ``ValueError`` if an account with the same ``id`` already
    exists — account IDs must be unique and are never reused.
    """
    if any(account.id == new_account.id for account in accounts):
        raise ValueError(f"account id '{new_account.id}' already exists")
    return [*accounts, new_account]


def update_account(
    accounts: list[Account],
    account_id: str,
    *,
    name: str,
    number: str,
    description: str,
    starting_balance: Decimal,
    account_type: AccountType,
    status: AccountStatus,
) -> list[Account]:
    """Return ``accounts`` with the account named ``account_id`` updated.

    The account's ``id`` itself is immutable and is not accepted here.
    Raises ``ValueError`` if no account with ``account_id`` exists.
    """
    if not any(account.id == account_id for account in accounts):
        raise ValueError(f"no account with id '{account_id}'")
    return [
        account.model_copy(
            update={
                "name": name,
                "number": number,
                "description": description,
                "starting_balance": starting_balance,
                "account_type": account_type,
                "status": status,
            }
        )
        if account.id == account_id
        else account
        for account in accounts
    ]


def remove_account(
    accounts: list[Account], transactions: list[Transaction], account_id: str
) -> list[Account]:
    """Return ``accounts`` with the account named ``account_id`` removed.

    Raises ``ValueError`` if no such account exists, or if any transaction
    still references it — deleting it would create an orphaned reference
    that the ledger consistency check would then flag.
    """
    if not any(account.id == account_id for account in accounts):
        raise ValueError(f"no account with id '{account_id}'")
    if any(transaction.account_id == account_id for transaction in transactions):
        raise ValueError(
            f"account '{account_id}' still has transactions and cannot be deleted"
        )
    return [account for account in accounts if account.id != account_id]
