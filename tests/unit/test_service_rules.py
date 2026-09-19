"""Tests for app.services.rules validation."""

from decimal import Decimal

import pytest

from app.models.transaction import TransactionType
from app.services.rules import (
    parse_amount_bound,
    parse_priority,
    parse_rule_type,
    validate_rule,
)


def test_parse_priority_blank_defaults_to_zero():
    assert parse_priority("") == 0


def test_parse_priority_parses_int():
    assert parse_priority("5") == 5


def test_parse_priority_rejects_non_integer():
    with pytest.raises(ValueError):
        parse_priority("abc")


def test_parse_amount_bound_blank_is_none():
    assert parse_amount_bound("", "minimum amount") is None


def test_parse_amount_bound_parses_decimal():
    assert parse_amount_bound("12.50", "minimum amount") == Decimal("12.50")


def test_parse_amount_bound_rejects_negative():
    with pytest.raises(ValueError):
        parse_amount_bound("-5", "minimum amount")


def test_parse_amount_bound_rejects_unparseable():
    with pytest.raises(ValueError):
        parse_amount_bound("abc", "minimum amount")


def test_parse_rule_type_blank_is_none():
    assert parse_rule_type("") is None


def test_parse_rule_type_parses_income_and_expense():
    assert parse_rule_type("income") is TransactionType.INCOME
    assert parse_rule_type("expense") is TransactionType.EXPENSE


def test_parse_rule_type_rejects_transfer():
    with pytest.raises(ValueError):
        parse_rule_type("transfer")


def test_parse_rule_type_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_rule_type("bogus")


def test_validate_rule_rejects_invalid_pattern():
    with pytest.raises(ValueError):
        validate_rule("(unclosed", "Groceries", "0", "", "", "", "")


def test_validate_rule_rejects_blank_category():
    with pytest.raises(ValueError):
        validate_rule("ZABKA", "", "0", "", "", "", "")


def test_validate_rule_collapses_exact_amount_to_min_and_max():
    _priority, min_value, max_value, _txn_type = validate_rule(
        "ZABKA", "Groceries", "0", "", "", "42.00", ""
    )

    assert min_value == Decimal("42.00")
    assert max_value == Decimal("42.00")


def test_validate_rule_rejects_exact_amount_combined_with_min():
    with pytest.raises(ValueError):
        validate_rule("ZABKA", "Groceries", "0", "10", "", "42.00", "")


def test_validate_rule_rejects_min_greater_than_max():
    with pytest.raises(ValueError):
        validate_rule("ZABKA", "Groceries", "0", "50", "10", "", "")


def test_validate_rule_returns_parsed_priority_and_type():
    priority, min_value, max_value, txn_type = validate_rule(
        "ZABKA", "Groceries", "5", "", "", "", "expense"
    )

    assert priority == 5
    assert min_value is None
    assert max_value is None
    assert txn_type is TransactionType.EXPENSE
