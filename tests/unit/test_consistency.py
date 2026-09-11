"""Tests for app.services.consistency.check_consistency."""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType
from app.services.consistency import check_consistency

CHECKING = Account(
    id="chk", name="Checking", number="1", description="", starting_balance=Decimal("0")
)
SAVINGS = Account(
    id="sav", name="Savings", number="2", description="", starting_balance=Decimal("0")
)


def _transaction(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 1, 1),
        "account_id": "chk",
        "category": "Groceries",
        "subcategory": "Supermarket",
        "description": "x",
        "amount": Decimal("-10"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_clean_ledger_has_no_issues():
    assert check_consistency([_transaction()], [CHECKING]) == []


def test_flags_transaction_with_unknown_account_id():
    issues = check_consistency([_transaction(account_id="ghost")], [CHECKING])

    assert [i.kind for i in issues] == ["invalid_account"]


def test_flags_transfer_row_missing_transfer_id():
    txn = _transaction(type=TransactionType.TRANSFER, transfer_id=None)

    issues = check_consistency([txn], [CHECKING])

    assert [i.kind for i in issues] == ["orphaned_transfer"]


def test_flags_transfer_id_with_only_one_row():
    txn = _transaction(
        type=TransactionType.TRANSFER, transfer_id="x1", amount=Decimal("-50")
    )

    issues = check_consistency([txn], [CHECKING])

    assert [i.kind for i in issues] == ["orphaned_transfer"]


def test_flags_transfer_pair_that_does_not_net_to_zero():
    outflow = _transaction(
        id="t1",
        account_id="chk",
        type=TransactionType.TRANSFER,
        transfer_id="x1",
        amount=Decimal("-50"),
    )
    inflow = _transaction(
        id="t2",
        account_id="sav",
        type=TransactionType.TRANSFER,
        transfer_id="x1",
        amount=Decimal("40"),
    )

    issues = check_consistency([outflow, inflow], [CHECKING, SAVINGS])

    assert [i.kind for i in issues] == ["orphaned_transfer"]


def test_valid_transfer_pair_has_no_issues():
    outflow = _transaction(
        id="t1",
        account_id="chk",
        type=TransactionType.TRANSFER,
        transfer_id="x1",
        amount=Decimal("-50"),
    )
    inflow = _transaction(
        id="t2",
        account_id="sav",
        type=TransactionType.TRANSFER,
        transfer_id="x1",
        amount=Decimal("50"),
    )

    assert check_consistency([outflow, inflow], [CHECKING, SAVINGS]) == []
