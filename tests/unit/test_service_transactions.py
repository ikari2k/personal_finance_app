"""Tests for app.services.transactions business rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.transaction import Transaction, TransactionType
from app.services.transactions import (
    ensure_category,
    new_transaction,
    new_transfer_pair,
    remove_transaction,
    update_transaction,
)

ACCOUNT_IDS = ["chk", "sav"]


def _txn(**overrides) -> Transaction:
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


def test_ensure_category_adds_new_category_and_subcategory():
    result = ensure_category({}, TransactionType.EXPENSE, "Groceries", "Supermarket")

    assert result == {"expense": {"Groceries": ["Supermarket"]}}


def test_ensure_category_does_not_duplicate_existing_subcategory():
    existing = {"expense": {"Groceries": ["Supermarket"]}}

    result = ensure_category(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket"
    )

    assert result == {"expense": {"Groceries": ["Supermarket"]}}


def test_ensure_category_does_not_mutate_the_input():
    existing = {"expense": {"Groceries": ["Supermarket"]}}

    ensure_category(existing, TransactionType.EXPENSE, "Groceries", "Restaurants")

    assert existing == {"expense": {"Groceries": ["Supermarket"]}}


def test_ensure_category_skips_blank_subcategory():
    result = ensure_category({}, TransactionType.EXPENSE, "Groceries", "")

    assert result == {"expense": {"Groceries": []}}


def test_ensure_category_keeps_income_and_expense_trees_separate():
    existing = {"expense": {"Groceries": ["Supermarket"]}}

    result = ensure_category(existing, TransactionType.INCOME, "Salary", "")

    assert result == {
        "expense": {"Groceries": ["Supermarket"]},
        "income": {"Salary": []},
    }


def test_ensure_category_rejects_transfer_type():
    with pytest.raises(ValueError):
        ensure_category({}, TransactionType.TRANSFER, "Transfer", "")


def test_new_transaction_normalizes_expense_to_negative():
    txn = new_transaction(
        ACCOUNT_IDS,
        account_id="chk",
        date=date(2026, 1, 1),
        category="Groceries",
        subcategory="Supermarket",
        description="x",
        amount=Decimal("42.50"),
        type=TransactionType.EXPENSE,
    )

    assert txn.amount == Decimal("-42.50")


def test_new_transaction_keeps_income_positive():
    txn = new_transaction(
        ACCOUNT_IDS,
        account_id="chk",
        date=date(2026, 1, 1),
        category="Income",
        subcategory="Salary",
        description="x",
        amount=Decimal("1000"),
        type=TransactionType.INCOME,
    )

    assert txn.amount == Decimal("1000")


def test_new_transaction_rejects_unknown_account():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="ghost",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_new_transaction_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("0"),
            type=TransactionType.EXPENSE,
        )


def test_new_transaction_rejects_transfer_type():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.TRANSFER,
        )


def test_new_transfer_pair_builds_opposite_sign_rows_sharing_transfer_id():
    outflow, inflow = new_transfer_pair(
        ACCOUNT_IDS,
        from_account_id="chk",
        to_account_id="sav",
        date=date(2026, 1, 1),
        amount=Decimal("100"),
    )

    assert outflow.transfer_id == inflow.transfer_id
    assert outflow.amount == Decimal("-100")
    assert inflow.amount == Decimal("100")
    assert outflow.account_id == "chk"
    assert inflow.account_id == "sav"
    assert outflow.type is TransactionType.TRANSFER
    assert inflow.type is TransactionType.TRANSFER


def test_new_transfer_pair_rejects_same_account_on_both_sides():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="chk",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_new_transfer_pair_rejects_unknown_account():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="ghost",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_new_transfer_pair_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="sav",
            date=date(2026, 1, 1),
            amount=Decimal("0"),
        )


def test_update_transaction_replaces_fields_but_keeps_the_id():
    existing = [_txn(id="t1", description="old")]

    updated = update_transaction(
        existing,
        "t1",
        ACCOUNT_IDS,
        account_id="sav",
        date=date(2026, 2, 2),
        category="Rent",
        subcategory="",
        description="new",
        amount=Decimal("20"),
        type=TransactionType.EXPENSE,
    )

    assert len(updated) == 1
    assert updated[0].id == "t1"
    assert updated[0].account_id == "sav"
    assert updated[0].description == "new"
    assert updated[0].amount == Decimal("-20")


def test_update_transaction_rejects_unknown_id():
    with pytest.raises(ValueError):
        update_transaction(
            [_txn(id="t1")],
            "ghost",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_update_transaction_rejects_editing_a_transfer_leg():
    transfer_leg = _txn(id="t1", type=TransactionType.TRANSFER, transfer_id="x1")

    with pytest.raises(ValueError):
        update_transaction(
            [transfer_leg],
            "t1",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_update_transaction_rejects_changing_type_to_transfer():
    with pytest.raises(ValueError):
        update_transaction(
            [_txn(id="t1")],
            "t1",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.TRANSFER,
        )


def test_remove_transaction_deletes_a_plain_row():
    existing = [_txn(id="t1"), _txn(id="t2")]

    result = remove_transaction(existing, "t1")

    assert [t.id for t in result] == ["t2"]


def test_remove_transaction_rejects_unknown_id():
    with pytest.raises(ValueError):
        remove_transaction([_txn(id="t1")], "ghost")


def test_remove_transaction_deletes_both_legs_of_a_transfer():
    outflow = _txn(
        id="t1", account_id="chk", type=TransactionType.TRANSFER, transfer_id="x1"
    )
    inflow = _txn(
        id="t2", account_id="sav", type=TransactionType.TRANSFER, transfer_id="x1"
    )
    other = _txn(id="t3")

    result = remove_transaction([outflow, inflow, other], "t1")

    assert [t.id for t in result] == ["t3"]
