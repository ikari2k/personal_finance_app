"""Tests for app.storage.rules read/write round-tripping."""

from decimal import Decimal

from app.models.rule import Rule
from app.models.transaction import TransactionType
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


def test_write_then_read_round_trips_amount_bounds(tmp_path):
    path = tmp_path / "rules.toml"
    rules = [
        Rule(pattern="ORLEN", category="Fuel", min_amount=Decimal("100.00")),
        Rule(pattern="ORLEN", category="Groceries", max_amount=Decimal("99.99")),
    ]

    write_rules(rules, path)

    assert read_rules(path) == rules


def test_write_then_read_round_trips_unset_amount_bounds_as_none(tmp_path):
    path = tmp_path / "rules.toml"
    rules = [Rule(pattern="ZABKA", category="Groceries")]

    write_rules(rules, path)
    [loaded] = read_rules(path)

    assert loaded.min_amount is None
    assert loaded.max_amount is None


def test_read_rules_tolerates_a_file_written_before_amount_bounds_existed(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(
        '[[rules]]\npattern = "ZABKA"\nfield = "description"\n'
        'category = "Groceries"\nsubcategory = ""\npriority = 0\n'
    )

    [loaded] = read_rules(path)

    assert loaded.pattern == "ZABKA"
    assert loaded.min_amount is None
    assert loaded.max_amount is None
    assert loaded.type is None


def test_write_then_read_round_trips_type(tmp_path):
    path = tmp_path / "rules.toml"
    rules = [
        Rule(pattern="ORLEN", category="Fuel", type=TransactionType.EXPENSE),
        Rule(pattern="ORLEN", category="Refund", type=TransactionType.INCOME),
    ]

    write_rules(rules, path)

    assert read_rules(path) == rules


def test_write_then_read_round_trips_unset_type_as_none(tmp_path):
    path = tmp_path / "rules.toml"
    rules = [Rule(pattern="ZABKA", category="Groceries")]

    write_rules(rules, path)
    [loaded] = read_rules(path)

    assert loaded.type is None
