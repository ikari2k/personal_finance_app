"""Tests for app.services.transactions business rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.transaction import TransactionType
from app.services.transactions import (
    ensure_category,
    new_transaction,
    new_transfer_pair,
)

ACCOUNT_IDS = ["chk", "sav"]


def test_ensure_category_adds_new_category_and_subcategory():
    result = ensure_category({}, "Groceries", "Supermarket")

    assert result == {"Groceries": ["Supermarket"]}


def test_ensure_category_does_not_duplicate_existing_subcategory():
    existing = {"Groceries": ["Supermarket"]}

    result = ensure_category(existing, "Groceries", "Supermarket")

    assert result == {"Groceries": ["Supermarket"]}


def test_ensure_category_does_not_mutate_the_input():
    existing = {"Groceries": ["Supermarket"]}

    ensure_category(existing, "Groceries", "Restaurants")

    assert existing == {"Groceries": ["Supermarket"]}


def test_ensure_category_skips_blank_subcategory():
    result = ensure_category({}, "Groceries", "")

    assert result == {"Groceries": []}


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
