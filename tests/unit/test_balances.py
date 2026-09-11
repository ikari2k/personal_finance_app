"""Tests for app.services.balances."""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType
from app.services.balances import account_balance, all_balances

CHECKING = Account(
    id="chk",
    name="Checking",
    number="1",
    description="",
    starting_balance=Decimal("100"),
)
SAVINGS = Account(
    id="sav",
    name="Savings",
    number="2",
    description="",
    starting_balance=Decimal("500"),
)


def _txn(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 1, 1),
        "account_id": "chk",
        "category": "c",
        "subcategory": "",
        "description": "",
        "amount": Decimal("-10"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_account_balance_is_starting_balance_with_no_transactions():
    assert account_balance(CHECKING, []) == Decimal("100")


def test_account_balance_sums_only_that_accounts_transactions():
    txns = [
        _txn(id="t1", account_id="chk", amount=Decimal("-10")),
        _txn(id="t2", account_id="chk", amount=Decimal("50")),
        _txn(id="t3", account_id="sav", amount=Decimal("1000")),
    ]

    assert account_balance(CHECKING, txns) == Decimal("140")


def test_all_balances_covers_every_account():
    txns = [
        _txn(id="t1", account_id="chk", amount=Decimal("-10")),
        _txn(id="t2", account_id="sav", amount=Decimal("25")),
    ]

    assert all_balances([CHECKING, SAVINGS], txns) == {
        "chk": Decimal("90"),
        "sav": Decimal("525"),
    }
