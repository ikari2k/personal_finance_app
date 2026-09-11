"""Account balance calculation: starting balance + transactions to date."""

from collections.abc import Iterable
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction


def account_balance(account: Account, transactions: Iterable[Transaction]) -> Decimal:
    """Return ``account``'s current balance: starting balance + its rows."""
    total = account.starting_balance
    for transaction in transactions:
        if transaction.account_id == account.id:
            total += transaction.amount
    return total


def all_balances(
    accounts: Iterable[Account], transactions: Iterable[Transaction]
) -> dict[str, Decimal]:
    """Return a mapping of account id to current balance for every account."""
    transactions = list(transactions)
    return {account.id: account_balance(account, transactions) for account in accounts}
