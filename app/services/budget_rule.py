"""Business rules for the 50/30/20 budget rule's targets.

Pure functions, no file I/O — see ``app.storage.budget_rule`` for persistence.
"""

from decimal import Decimal

from app.models.budget_rule import BudgetRuleTargets
from app.services.aggregation import BudgetRuleSplit


def validate_targets(targets: BudgetRuleTargets) -> BudgetRuleTargets:
    """Return ``targets`` unchanged if valid.

    Raises ``ValueError`` if any percentage is outside 0-100 or the three
    don't sum to exactly 100.
    """
    for label, value in (
        ("needs", targets.needs),
        ("wants", targets.wants),
        ("savings", targets.savings),
    ):
        if not 0 <= value <= 100:
            raise ValueError(f"{label} target must be between 0 and 100")
    total = targets.needs + targets.wants + targets.savings
    if total != 100:
        raise ValueError(f"targets must sum to 100 (got {total})")
    return targets


def rule_met(split: BudgetRuleSplit, targets: BudgetRuleTargets) -> bool:
    """Return whether a closed period met the budget rule.

    Savings must reach its target, and Needs and Wants must each stay within
    theirs *even if every unclassified expense belonged to that bucket* —
    untagged spend is unknown, so it must not be allowed to flatter the
    result. With nothing unclassified this is the plain comparison. A period
    with no income has no percentages and so never counts as met.
    """
    if split.needs_pct is None:
        return False
    unclassified = split.unclassified_pct or Decimal("0")
    return (
        split.savings_pct >= targets.savings
        and split.needs_pct + unclassified <= targets.needs
        and split.wants_pct + unclassified <= targets.wants
    )
