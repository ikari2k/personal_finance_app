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


def _split(income, needs, wants, savings, unclassified="0"):
    from decimal import Decimal

    from app.services.aggregation import BudgetRuleSplit

    def pct(x):
        return (Decimal(x) / Decimal(income) * 100).quantize(Decimal("0.1"))

    return BudgetRuleSplit(
        income=Decimal(income),
        needs=Decimal(needs),
        wants=Decimal(wants),
        unclassified=Decimal(unclassified),
        savings=Decimal(savings),
        needs_pct=pct(needs),
        wants_pct=pct(wants),
        unclassified_pct=pct(unclassified),
        savings_pct=pct(savings),
    )


def test_rule_met_when_all_three_targets_hold():
    from app.services.budget_rule import rule_met

    assert rule_met(_split("1000", "500", "300", "200"), BudgetRuleTargets())
    assert rule_met(_split("1000", "400", "200", "400"), BudgetRuleTargets())


def test_rule_missed_when_any_target_fails():
    from app.services.budget_rule import rule_met

    targets = BudgetRuleTargets()
    assert not rule_met(_split("1000", "510", "290", "200"), targets)  # needs over
    assert not rule_met(_split("1000", "400", "350", "250"), targets)  # wants over
    assert not rule_met(_split("1000", "500", "310", "190"), targets)  # savings short
    assert not rule_met(_split("1000", "400", "200", "-50"), targets)  # withdrawal


def test_unclassified_spend_counts_against_the_rule_in_the_worst_case():
    from app.services.budget_rule import rule_met

    targets = BudgetRuleTargets()
    # Needs 45% + wants 25% look fine, but 10% is untagged: if it were all
    # wants, wants would be 35% > 30%, so the rule is not provably met.
    assert not rule_met(_split("1000", "450", "250", "200", "100"), targets)
    # Small enough to be harmless in either bucket.
    assert rule_met(_split("1000", "450", "250", "250", "20"), targets)


def test_no_income_is_never_met():
    from decimal import Decimal

    from app.services.aggregation import BudgetRuleSplit
    from app.services.budget_rule import rule_met

    empty = BudgetRuleSplit(
        Decimal("0"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        None,
        None,
        None,
        None,
    )
    assert not rule_met(empty, BudgetRuleTargets())
