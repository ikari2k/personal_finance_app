"""Business rules for the 50/30/20 budget rule's targets.

Pure functions, no file I/O — see ``app.storage.budget_rule`` for persistence.
"""

from app.models.budget_rule import BudgetRuleTargets


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
