"""Tests for app.storage.rules read/write round-tripping."""

from app.models.rule import Rule
from app.storage.rules import read_rules, write_rules


def test_read_rules_returns_empty_list_when_file_missing(tmp_path):
    assert read_rules(tmp_path / "rules.toml") == []


def test_write_then_read_round_trips_rules(tmp_path):
    path = tmp_path / "rules.toml"
    rules = [
        Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket"),
        Rule(pattern="NETFLIX", category="Entertainment", priority=5),
    ]

    write_rules(rules, path)

    assert read_rules(path) == rules
