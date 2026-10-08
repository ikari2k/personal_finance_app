"""Tests for app.services.categories business rules."""

from decimal import Decimal

import pytest

from app.models.transaction import TransactionType
from app.services.categories import (
    add_category,
    add_subcategory,
    category_pair_exists,
    delete_category,
    delete_subcategory,
    subcategories_exceed_category_budget,
    update_category,
    update_subcategory,
)


def test_add_category_adds_entry_with_icon():
    result = add_category({}, TransactionType.EXPENSE, "Groceries", "cart")

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "cart",
                "budget": "",
                "bucket": "",
                "subcategories": {},
            }
        }
    }


def test_add_category_defaults_to_no_icon():
    result = add_category({}, TransactionType.EXPENSE, "Groceries")

    assert result == {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }


def test_add_category_accepts_budget():
    result = add_category(
        {}, TransactionType.EXPENSE, "Groceries", budget=Decimal("500.00")
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "500.00",
                "bucket": "",
                "subcategories": {},
            }
        }
    }


def test_add_category_rejects_negative_budget():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.EXPENSE, "Groceries", budget=Decimal("-1.00"))


def test_add_category_rejects_budget_for_income():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.INCOME, "Salary", budget=Decimal("500.00"))


def test_add_category_rejects_blank_name():
    with pytest.raises(ValueError):
        add_category({}, TransactionType.EXPENSE, "  ")


def test_add_category_rejects_duplicate_name():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

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
            "Groceries": {
                "icon": "cart",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "store", "budget": "", "bucket": ""}
                },
            }
        }
    }

    result = update_category(
        existing, TransactionType.EXPENSE, "Groceries", name="Food", icon="utensils"
    )

    assert result == {
        "expense": {
            "Food": {
                "icon": "utensils",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "store", "budget": "", "bucket": ""}
                },
            }
        }
    }


def test_update_category_accepts_budget():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    result = update_category(
        existing,
        TransactionType.EXPENSE,
        "Groceries",
        name="Groceries",
        budget=Decimal("300"),
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "300",
                "bucket": "",
                "subcategories": {},
            }
        }
    }


def test_update_category_rejects_negative_budget():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        update_category(
            existing,
            TransactionType.EXPENSE,
            "Groceries",
            name="Groceries",
            budget=Decimal("-5"),
        )


def test_update_category_rejects_budget_for_income():
    existing = {
        "income": {
            "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        update_category(
            existing,
            TransactionType.INCOME,
            "Salary",
            name="Salary",
            budget=Decimal("100"),
        )


def test_update_category_rejects_unknown_current_name():
    with pytest.raises(ValueError):
        update_category({"expense": {}}, TransactionType.EXPENSE, "Ghost", name="X")


def test_update_category_rejects_renaming_onto_a_different_existing_category():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
            "Housing": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
        }
    }

    with pytest.raises(ValueError):
        update_category(existing, TransactionType.EXPENSE, "Groceries", name="Housing")


def test_update_category_allows_keeping_the_same_name():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    result = update_category(
        existing, TransactionType.EXPENSE, "Groceries", name="Groceries", icon="cart"
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "cart",
                "budget": "",
                "bucket": "",
                "subcategories": {},
            }
        }
    }


def test_delete_category_removes_it_and_its_subcategories():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
    }

    result = delete_category(existing, TransactionType.EXPENSE, "Groceries")

    assert result == {"expense": {}}


def test_delete_category_rejects_unknown_name():
    with pytest.raises(ValueError):
        delete_category({"expense": {}}, TransactionType.EXPENSE, "Ghost")


def test_add_subcategory_adds_entry_with_icon():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    result = add_subcategory(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket", "store"
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "store", "budget": "", "bucket": ""}
                },
            }
        }
    }


def test_add_subcategory_accepts_budget():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    result = add_subcategory(
        existing,
        TransactionType.EXPENSE,
        "Groceries",
        "Supermarket",
        budget=Decimal("150"),
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "150", "bucket": ""}
                },
            }
        }
    }


def test_add_subcategory_rejects_negative_budget():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        add_subcategory(
            existing,
            TransactionType.EXPENSE,
            "Groceries",
            "Supermarket",
            budget=Decimal("-1"),
        )


def test_add_subcategory_rejects_budget_for_income():
    existing = {
        "income": {
            "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        add_subcategory(
            existing,
            TransactionType.INCOME,
            "Salary",
            "Bonus",
            budget=Decimal("100"),
        )


def test_add_subcategory_rejects_unknown_category():
    with pytest.raises(ValueError):
        add_subcategory({"expense": {}}, TransactionType.EXPENSE, "Ghost", "Sub")


def test_add_subcategory_rejects_duplicate_name():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
    }

    with pytest.raises(ValueError):
        add_subcategory(existing, TransactionType.EXPENSE, "Groceries", "Supermarket")


def test_add_subcategory_rejects_unknown_icon():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        add_subcategory(
            existing, TransactionType.EXPENSE, "Groceries", "Supermarket", "bogus"
        )


def test_update_subcategory_renames_and_reicons():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
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
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Store": {"icon": "store", "budget": "", "bucket": ""}
                },
            }
        }
    }


def test_update_subcategory_accepts_budget():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
    }

    result = update_subcategory(
        existing,
        TransactionType.EXPENSE,
        "Groceries",
        "Supermarket",
        name="Supermarket",
        budget=Decimal("75"),
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "75", "bucket": ""}
                },
            }
        }
    }


def test_update_subcategory_rejects_negative_budget():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
    }

    with pytest.raises(ValueError):
        update_subcategory(
            existing,
            TransactionType.EXPENSE,
            "Groceries",
            "Supermarket",
            name="Supermarket",
            budget=Decimal("-1"),
        )


def test_update_subcategory_rejects_unknown_current_name():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        update_subcategory(
            existing, TransactionType.EXPENSE, "Groceries", "Ghost", name="X"
        )


def test_delete_subcategory_removes_it():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": "", "bucket": ""},
                    "Farmers Market": {"icon": "", "budget": "", "bucket": ""},
                },
            }
        }
    }

    result = delete_subcategory(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket"
    )

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "bucket": "",
                "subcategories": {
                    "Farmers Market": {"icon": "", "budget": "", "bucket": ""}
                },
            }
        }
    }


def test_delete_subcategory_rejects_unknown_name():
    existing = {
        "expense": {
            "Groceries": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
        }
    }

    with pytest.raises(ValueError):
        delete_subcategory(existing, TransactionType.EXPENSE, "Groceries", "Ghost")


def test_subcategories_exceed_category_budget_when_sum_is_greater():
    entry = {
        "icon": "",
        "budget": "100",
        "bucket": "",
        "subcategories": {
            "A": {"icon": "", "budget": "50", "bucket": ""},
            "B": {"icon": "", "budget": "30", "bucket": ""},
            "C": {"icon": "", "budget": "45", "bucket": ""},
        },
    }

    assert subcategories_exceed_category_budget(entry) is True


def test_subcategories_exceed_category_budget_when_sum_is_within():
    entry = {
        "icon": "",
        "budget": "100",
        "bucket": "",
        "subcategories": {
            "A": {"icon": "", "budget": "50", "bucket": ""},
            "B": {"icon": "", "budget": "30", "bucket": ""},
        },
    }

    assert subcategories_exceed_category_budget(entry) is False


def test_subcategories_exceed_category_budget_ignores_unbudgeted_subcategories():
    entry = {
        "icon": "",
        "budget": "10",
        "bucket": "",
        "subcategories": {"A": {"icon": "", "budget": "", "bucket": ""}},
    }

    assert subcategories_exceed_category_budget(entry) is False


def test_subcategories_exceed_category_budget_false_when_category_has_no_budget():
    entry = {
        "icon": "",
        "budget": "",
        "bucket": "",
        "subcategories": {"A": {"icon": "", "budget": "999", "bucket": ""}},
    }

    assert subcategories_exceed_category_budget(entry) is False


_TREE = {
    "expense": {
        "Groceries": {
            "icon": "",
            "budget": "",
            "bucket": "",
            "subcategories": {"Supermarket": {"icon": "", "budget": "", "bucket": ""}},
        }
    }
}


def test_category_pair_exists_true_for_bare_category():
    assert category_pair_exists(_TREE, TransactionType.EXPENSE, "Groceries", "") is True


def test_category_pair_exists_true_for_real_subcategory():
    assert (
        category_pair_exists(_TREE, TransactionType.EXPENSE, "Groceries", "Supermarket")
        is True
    )


def test_category_pair_exists_false_for_unknown_category():
    assert category_pair_exists(_TREE, TransactionType.EXPENSE, "Ghost", "") is False


def test_category_pair_exists_false_for_unknown_subcategory():
    assert (
        category_pair_exists(_TREE, TransactionType.EXPENSE, "Groceries", "Ghost")
        is False
    )


def test_category_pair_exists_false_for_wrong_type():
    assert category_pair_exists(_TREE, TransactionType.INCOME, "Groceries", "") is False


def _tree_with_buckets():
    cats = {"income": {}, "expense": {}}
    cats = add_category(cats, TransactionType.EXPENSE, "Food", bucket="need")
    return add_subcategory(
        cats, TransactionType.EXPENSE, "Food", "Restaurants", bucket="want"
    )


def test_add_category_and_subcategory_store_bucket():
    cats = _tree_with_buckets()
    assert cats["expense"]["Food"]["bucket"] == "need"
    assert cats["expense"]["Food"]["subcategories"]["Restaurants"]["bucket"] == "want"


def test_bucket_defaults_to_unclassified():
    cats = add_category({"income": {}, "expense": {}}, TransactionType.EXPENSE, "X")
    assert cats["expense"]["X"]["bucket"] == ""


def test_update_category_without_bucket_keeps_it():
    cats = update_category(
        _tree_with_buckets(),
        TransactionType.EXPENSE,
        "Food",
        name="Eating",
        icon="cart",
    )
    assert cats["expense"]["Eating"]["bucket"] == "need"


def test_update_category_with_empty_bucket_clears_it():
    cats = update_category(
        _tree_with_buckets(), TransactionType.EXPENSE, "Food", name="Food", bucket=""
    )
    assert cats["expense"]["Food"]["bucket"] == ""


def test_update_subcategory_without_bucket_keeps_it_through_rename():
    cats = update_subcategory(
        _tree_with_buckets(),
        TransactionType.EXPENSE,
        "Food",
        "Restaurants",
        name="Dining",
    )
    assert cats["expense"]["Food"]["subcategories"]["Dining"]["bucket"] == "want"


def test_invalid_bucket_rejected():
    with pytest.raises(ValueError, match="bucket"):
        add_category(
            {"income": {}, "expense": {}},
            TransactionType.EXPENSE,
            "X",
            bucket="excluded",
        )
    with pytest.raises(ValueError, match="bucket"):
        add_category(
            {"income": {}, "expense": {}}, TransactionType.INCOME, "X", bucket="need"
        )


def test_income_category_can_be_excluded_but_not_its_subcategories():
    cats = add_category(
        {"income": {}, "expense": {}},
        TransactionType.INCOME,
        "Refunds",
        bucket="excluded",
    )
    assert cats["income"]["Refunds"]["bucket"] == "excluded"
    with pytest.raises(ValueError, match="bucket"):
        add_subcategory(
            cats, TransactionType.INCOME, "Refunds", "Tax", bucket="excluded"
        )
