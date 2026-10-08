"""Domain model for the 50/30/20 budget rule's target percentages."""

from pydantic import BaseModel


class BudgetRuleTargets(BaseModel):
    """Target share of income for each bucket, as whole percentages.

    Defaults to the classic 50/30/20. The three must sum to 100 — enforced
    in ``services.budget_rule.validate_targets``, not here, so a hand-edited
    file that drifts still loads and the page can show a clear error.
    """

    needs: int = 50
    wants: int = 30
    savings: int = 20
