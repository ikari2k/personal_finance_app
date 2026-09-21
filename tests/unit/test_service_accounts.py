"""Tests for app.services.accounts business rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account, AccountStatus, AccountType
from app.models.transaction import Transaction, TransactionType
from app.services.accounts import add_account, remove_account, update_account

CHECKING = Account(
    id="chk",
    name="Checking",
    number="1",
    description="",
    starting_balance=Decimal("100"),
)


def test_add_account_appends_new_account():
    assert add_account([], CHECKING) == [CHECKING]


def test_add_account_rejects_duplicate_id():
    duplicate = CHECKING.model_copy(update={"name": "Other"})

    with pytest.raises(ValueError):
        add_account([CHECKING], duplicate)


def test_update_account_changes_fields_but_not_id():
    updated = update_account(
        [CHECKING],
        "chk",
        name="New Name",
        number="2",
        description="desc",
        starting_balance=Decimal("200"),
        account_type=AccountType.SAVINGS,
        status=AccountStatus.CLOSED,
    )

    assert updated == [
        Account(
            id="chk",
            name="New Name",
            number="2",
            description="desc",
            starting_balance=Decimal("200"),
            account_type=AccountType.SAVINGS,
            status=AccountStatus.CLOSED,
        )
    ]


def test_update_account_raises_for_unknown_id():
    with pytest.raises(ValueError):
        update_account(
            [CHECKING],
            "ghost",
            name="x",
            number="",
            description="",
            starting_balance=Decimal("0"),
            account_type=AccountType.OTHER,
            status=AccountStatus.ACTIVE,
        )


def test_remove_account_deletes_when_unreferenced():
    assert remove_account([CHECKING], [], "chk") == []


def test_remove_account_raises_for_unknown_id():
    with pytest.raises(ValueError):
        remove_account([CHECKING], [], "ghost")


def test_remove_account_raises_when_referenced_by_a_transaction():
    txn = Transaction(
        id="t1",
        date=date(2026, 1, 1),
        account_id="chk",
        category="c",
        subcategory="",
        description="",
        amount=Decimal("-10"),
        type=TransactionType.EXPENSE,
    )

    with pytest.raises(ValueError):
        remove_account([CHECKING], [txn], "chk")
