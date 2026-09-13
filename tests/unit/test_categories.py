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
            "Salary": {"icon": "banknote", "subcategories": {}},
            "Freelance": {"icon": "", "subcategories": {}},
        },
        "expense": {
            "Groceries": {
                "icon": "cart",
                "subcategories": {"Supermarket": "store", "Restaurants": ""},
            },
            "Utilities": {"icon": "", "subcategories": {"Electric": "", "Water": ""}},
            "Miscellaneous": {"icon": "", "subcategories": {}},
        },
    }

    write_categories(categories, path)

    assert read_categories(path) == categories


def test_income_and_expense_trees_stay_independent(tmp_path):
    path = tmp_path / "categories.toml"
    categories = {
        "income": {"Salary": {"icon": "", "subcategories": {}}},
        "expense": {"Groceries": {"icon": "", "subcategories": {"Supermarket": ""}}},
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
        "income": {"Salary": {"icon": "", "subcategories": {}}},
        "expense": {
            "Groceries": {
                "icon": "",
                "subcategories": {"Supermarket": "", "Farmers Market": ""},
            }
        },
    }
