"""Tests for 50/30/20 target storage and validation."""

import pytest

from app.models.budget_rule import BudgetRuleTargets
from app.services.budget_rule import validate_targets
from app.storage.budget_rule import read_targets, write_targets


def test_read_missing_file_returns_defaults(tmp_path):
    assert read_targets(tmp_path / "nope.toml") == BudgetRuleTargets(
        needs=50, wants=30, savings=20
    )


def test_write_then_read_round_trips(tmp_path):
    path = tmp_path / "budget_rule.toml"
    write_targets(BudgetRuleTargets(needs=60, wants=20, savings=20), path)
    assert read_targets(path) == BudgetRuleTargets(needs=60, wants=20, savings=20)


def test_read_partial_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "budget_rule.toml"
    path.write_text("needs = 55\n")
    assert read_targets(path) == BudgetRuleTargets(needs=55, wants=30, savings=20)


def test_validate_accepts_default():
    targets = BudgetRuleTargets()
    assert validate_targets(targets) is targets


def test_validate_rejects_wrong_sum():
    with pytest.raises(ValueError, match="sum to 100"):
        validate_targets(BudgetRuleTargets(needs=50, wants=30, savings=30))


def test_validate_rejects_out_of_range():
    with pytest.raises(ValueError, match="between 0 and 100"):
        validate_targets(BudgetRuleTargets(needs=-10, wants=60, savings=50))
