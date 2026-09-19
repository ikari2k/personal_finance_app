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
    """Return a mapping of account id to current balance for every account.

    One pass over ``transactions`` (bucketed by ``account_id``) rather
    than calling ``account_balance`` once per account — that would be
    O(accounts × transactions); this is O(accounts + transactions).
    """
    balances = {account.id: account.starting_balance for account in accounts}
    for transaction in transactions:
        if transaction.account_id in balances:
            balances[transaction.account_id] += transaction.amount
    return balances
