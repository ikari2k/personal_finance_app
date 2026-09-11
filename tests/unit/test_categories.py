"""Tests for app.storage.categories read/write round-tripping."""

from app.storage.categories import read_categories, write_categories


def test_read_categories_returns_empty_dict_when_file_missing(tmp_path):
    assert read_categories(tmp_path / "categories.toml") == {}


def test_write_then_read_round_trips_categories(tmp_path):
    path = tmp_path / "categories.toml"
    categories = {
        "Groceries": ["Supermarket", "Restaurants"],
        "Utilities": ["Electric", "Water"],
        "Miscellaneous": [],
    }

    write_categories(categories, path)

    assert read_categories(path) == categories
