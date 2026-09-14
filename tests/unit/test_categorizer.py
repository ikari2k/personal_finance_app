"""Tests for app.services.categorizer."""

import pytest

from app.models.rule import Rule
from app.services.categorizer import categorize


def test_categorize_returns_first_matching_rule():
    rules = [Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket")]

    assert categorize("ZABKA Z8540 K.2", rules) == ("Groceries", "Supermarket")


def test_categorize_is_case_insensitive():
    rules = [Rule(pattern="netflix", category="Entertainment")]

    assert categorize("NETFLIX.COM", rules) == ("Entertainment", "")


def test_categorize_returns_blank_when_nothing_matches():
    rules = [Rule(pattern="ZABKA", category="Groceries")]

    assert categorize("APPLE.COM/BILL", rules) == ("", "")


def test_categorize_returns_blank_with_no_rules():
    assert categorize("anything", []) == ("", "")


def test_categorize_prefers_higher_priority_rule():
    rules = [
        Rule(pattern="A", category="Broad", priority=0),
        Rule(pattern="AUCHAN", category="Groceries", priority=10),
    ]

    assert categorize("AUCHAN 1035 KRAKOW", rules) == ("Groceries", "")


def test_categorize_ignores_rules_for_other_fields():
    rules = [Rule(pattern="ZABKA", field="amount", category="Groceries")]

    assert categorize("ZABKA Z8540 K.2", rules) == ("", "")


def test_categorize_raises_on_invalid_regex():
    rules = [Rule(pattern="[", category="Groceries")]

    with pytest.raises(ValueError):
        categorize("anything", rules)
