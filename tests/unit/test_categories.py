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
        "income": {"Salary": [], "Freelance": []},
        "expense": {
            "Groceries": ["Supermarket", "Restaurants"],
            "Utilities": ["Electric", "Water"],
            "Miscellaneous": [],
        },
    }

    write_categories(categories, path)

    assert read_categories(path) == categories


def test_income_and_expense_trees_stay_independent(tmp_path):
    path = tmp_path / "categories.toml"
    categories = {"income": {"Salary": []}, "expense": {"Groceries": ["Supermarket"]}}

    write_categories(categories, path)
    result = read_categories(path)

    assert "Salary" not in result["expense"]
    assert "Groceries" not in result["income"]
