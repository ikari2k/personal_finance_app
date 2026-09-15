"""Tests for app.services.categorizer."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType
from app.services.categorizer import (
    apply_reclassification,
    categorize,
    compile_pattern,
    plan_reclassification,
)


def _txn(
    id: str,
    description: str,
    category: str,
    subcategory: str = "",
    type: TransactionType = TransactionType.EXPENSE,
) -> Transaction:
    return Transaction(
        id=id,
        date=date(2026, 1, 1),
        account_id="chk",
        category=category,
        subcategory=subcategory,
        description=description,
        amount=Decimal("-10.00"),
        type=type,
    )


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


def test_compile_pattern_raises_on_invalid_regex():
    with pytest.raises(ValueError):
        compile_pattern("[")


def test_compile_pattern_returns_compiled_pattern_on_valid_regex():
    assert compile_pattern("ZABKA").search("ZABKA Z8540") is not None


def test_plan_reclassification_flags_a_row_a_rule_would_recategorize():
    rules = [Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket")]
    txn = _txn("t1", "ZABKA Z8540", category="Uncategorized")

    changes = plan_reclassification([txn], rules)

    assert len(changes) == 1
    change = changes[0]
    assert change.transaction_id == "t1"
    assert change.old_category == "Uncategorized"
    assert change.old_subcategory == ""
    assert change.new_category == "Groceries"
    assert change.new_subcategory == "Supermarket"


def test_plan_reclassification_skips_a_row_already_correctly_categorized():
    rules = [Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket")]
    txn = _txn("t1", "ZABKA Z8540", category="Groceries", subcategory="Supermarket")

    assert plan_reclassification([txn], rules) == []


def test_plan_reclassification_skips_a_row_no_rule_matches():
    rules = [Rule(pattern="ZABKA", category="Groceries")]
    txn = _txn("t1", "APPLE.COM/BILL", category="Uncategorized")

    assert plan_reclassification([txn], rules) == []


def test_plan_reclassification_skips_transfers():
    rules = [Rule(pattern=".*", category="Groceries")]
    txn = _txn(
        "t1",
        "Monthly savings transfer",
        category="Transfer",
        type=TransactionType.TRANSFER,
    )

    assert plan_reclassification([txn], rules) == []


def test_apply_reclassification_updates_only_the_changed_rows():
    rules = [Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket")]
    matching = _txn("t1", "ZABKA Z8540", category="Uncategorized")
    other = _txn("t2", "APPLE.COM/BILL", category="Uncategorized")
    changes = plan_reclassification([matching, other], rules)

    updated = apply_reclassification([matching, other], changes)

    updated_matching = next(t for t in updated if t.id == "t1")
    updated_other = next(t for t in updated if t.id == "t2")
    assert updated_matching.category == "Groceries"
    assert updated_matching.subcategory == "Supermarket"
    assert updated_other.category == "Uncategorized"


def test_apply_reclassification_skips_a_change_whose_row_no_longer_exists():
    rules = [Rule(pattern="ZABKA", category="Groceries")]
    matching = _txn("t1", "ZABKA Z8540", category="Uncategorized")
    changes = plan_reclassification([matching], rules)

    updated = apply_reclassification([], changes)

    assert updated == []
