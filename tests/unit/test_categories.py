"""Tests for app.storage.categories read/write round-tripping."""

from app.storage.categories import read_categories, write_categories


def test_read_categories_returns_empty_trees_when_file_missing(tmp_path):
    assert read_categories(tmp_path / "categories.toml") == {
        "income": {},
        "expense": {},
    }


def test_write_then_read_round_trips_categories(tmp_path):
    path = tmp_path / "categories.toml"
    categories = {
        "income": {
            "Salary": {"icon": "banknote", "budget": "", "subcategories": {}},
            "Freelance": {"icon": "", "budget": "", "subcategories": {}},
        },
        "expense": {
            "Groceries": {
                "icon": "cart",
                "budget": "400.00",
                "subcategories": {
                    "Supermarket": {"icon": "store", "budget": "300.00"},
                    "Restaurants": {"icon": "", "budget": ""},
                },
            },
            "Utilities": {
                "icon": "",
                "budget": "",
                "subcategories": {
                    "Electric": {"icon": "", "budget": ""},
                    "Water": {"icon": "", "budget": ""},
                },
            },
            "Miscellaneous": {"icon": "", "budget": "", "subcategories": {}},
        },
    }

    write_categories(categories, path)

    assert read_categories(path) == categories


def test_income_and_expense_trees_stay_independent(tmp_path):
    path = tmp_path / "categories.toml"
    categories = {
        "income": {"Salary": {"icon": "", "budget": "", "subcategories": {}}},
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        },
    }

    write_categories(categories, path)
    result = read_categories(path)

    assert "Salary" not in result["expense"]
    assert "Groceries" not in result["income"]


def test_read_categories_upgrades_pre_icon_flat_list_format(tmp_path):
    """Files from before the icon feature store a bare subcategory-name list.

    ``read_categories`` should upgrade that in memory (no separate
    migration step needed) rather than choke on it or drop the data.
    """
    path = tmp_path / "categories.toml"
    path.write_text(
        "[income]\nSalary = []\n\n"
        '[expense]\nGroceries = ["Supermarket", "Farmers Market"]\n'
    )

    result = read_categories(path)

    assert result == {
        "income": {"Salary": {"icon": "", "budget": "", "subcategories": {}}},
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {
                    "Supermarket": {"icon": "", "budget": ""},
                    "Farmers Market": {"icon": "", "budget": ""},
                },
            }
        },
    }


def test_read_categories_upgrades_pre_budget_flat_icon_subcategory_format(tmp_path):
    """Files from before the budget feature store a subcategory as a bare icon string.

    ``read_categories`` should upgrade that in memory (no separate
    migration step needed) rather than choke on it or drop the data.
    """
    path = tmp_path / "categories.toml"
    path.write_text(
        "[expense.Groceries]\n"
        'icon = "cart"\n\n'
        "[expense.Groceries.subcategories]\n"
        'Supermarket = "store"\n'
        'Restaurants = ""\n'
    )

    result = read_categories(path)

    assert result == {
        "income": {},
        "expense": {
            "Groceries": {
                "icon": "cart",
                "budget": "",
                "subcategories": {
                    "Supermarket": {"icon": "store", "budget": ""},
                    "Restaurants": {"icon": "", "budget": ""},
                },
            }
        },
    }
