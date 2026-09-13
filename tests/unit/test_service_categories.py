"""Tests for app.services.categories business rules."""

import pytest

from app.models.transaction import TransactionType
from app.services.categories import (
    add_category,
    add_subcategory,
    delete_category,
    delete_subcategory,
    update_category,
    update_subcategory,
)


def test_add_category_adds_entry_with_icon():
    result = add_category({}, TransactionType.EXPENSE, "Groceries", "cart")

    assert result == {"expense": {"Groceries": {"icon": "cart", "subcategories": {}}}}


def test_add_category_defaults_to_no_icon():
    result = add_category({}, TransactionType.EXPENSE, "Groceries")

    assert result == {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}


def test_add_category_rejects_blank_name():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.EXPENSE, "  ")


def test_add_category_rejects_duplicate_name():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    with pytest.raises(ValueError):
        add_category(existing, TransactionType.EXPENSE, "Groceries")


def test_add_category_rejects_unknown_icon():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.EXPENSE, "Groceries", "not-a-real-icon")


def test_add_category_rejects_transfer_type():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.TRANSFER, "Transfer")


def test_add_category_does_not_mutate_input():
    existing = {"expense": {}}

    add_category(existing, TransactionType.EXPENSE, "Groceries")

    assert existing == {"expense": {}}


def test_update_category_renames_and_reicons_keeping_subcategories():
    existing = {
        "expense": {
            "Groceries": {"icon": "cart", "subcategories": {"Supermarket": "store"}}
        }
    }

    result = update_category(
        existing, TransactionType.EXPENSE, "Groceries", name="Food", icon="utensils"
    )

    assert result == {
        "expense": {
            "Food": {"icon": "utensils", "subcategories": {"Supermarket": "store"}}
        }
    }


def test_update_category_rejects_unknown_current_name():
    with pytest.raises(ValueError):
        update_category({"expense": {}}, TransactionType.EXPENSE, "Ghost", name="X")


def test_update_category_rejects_renaming_onto_a_different_existing_category():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "subcategories": {}},
            "Housing": {"icon": "", "subcategories": {}},
        }
    }

    with pytest.raises(ValueError):
        update_category(existing, TransactionType.EXPENSE, "Groceries", name="Housing")


def test_update_category_allows_keeping_the_same_name():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    result = update_category(
        existing, TransactionType.EXPENSE, "Groceries", name="Groceries", icon="cart"
    )

    assert result == {"expense": {"Groceries": {"icon": "cart", "subcategories": {}}}}


def test_delete_category_removes_it_and_its_subcategories():
    existing = {
        "expense": {"Groceries": {"icon": "", "subcategories": {"Supermarket": ""}}}
    }

    result = delete_category(existing, TransactionType.EXPENSE, "Groceries")

    assert result == {"expense": {}}


def test_delete_category_rejects_unknown_name():
    with pytest.raises(ValueError):
        delete_category({"expense": {}}, TransactionType.EXPENSE, "Ghost")


def test_add_subcategory_adds_entry_with_icon():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    result = add_subcategory(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket", "store"
    )

    assert result == {
        "expense": {
            "Groceries": {"icon": "", "subcategories": {"Supermarket": "store"}}
        }
    }


def test_add_subcategory_rejects_unknown_category():
    with pytest.raises(ValueError):
        add_subcategory({"expense": {}}, TransactionType.EXPENSE, "Ghost", "Sub")


def test_add_subcategory_rejects_duplicate_name():
    existing = {
        "expense": {"Groceries": {"icon": "", "subcategories": {"Supermarket": ""}}}
    }

    with pytest.raises(ValueError):
        add_subcategory(existing, TransactionType.EXPENSE, "Groceries", "Supermarket")


def test_add_subcategory_rejects_unknown_icon():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    with pytest.raises(ValueError):
        add_subcategory(
            existing, TransactionType.EXPENSE, "Groceries", "Supermarket", "bogus"
        )


def test_update_subcategory_renames_and_reicons():
    existing = {
        "expense": {"Groceries": {"icon": "", "subcategories": {"Supermarket": ""}}}
    }

    result = update_subcategory(
        existing,
        TransactionType.EXPENSE,
        "Groceries",
        "Supermarket",
        name="Store",
        icon="store",
    )

    assert result == {
        "expense": {"Groceries": {"icon": "", "subcategories": {"Store": "store"}}}
    }


def test_update_subcategory_rejects_unknown_current_name():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    with pytest.raises(ValueError):
        update_subcategory(
            existing, TransactionType.EXPENSE, "Groceries", "Ghost", name="X"
        )


def test_delete_subcategory_removes_it():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "subcategories": {"Supermarket": "", "Farmers Market": ""},
            }
        }
    }

    result = delete_subcategory(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket"
    )

    assert result == {
        "expense": {"Groceries": {"icon": "", "subcategories": {"Farmers Market": ""}}}
    }


def test_delete_subcategory_rejects_unknown_name():
    existing = {"expense": {"Groceries": {"icon": "", "subcategories": {}}}}

    with pytest.raises(ValueError):
        delete_subcategory(existing, TransactionType.EXPENSE, "Groceries", "Ghost")
