"""Apply auto-categorization rules to a transaction description.

Pure functions over in-memory data — no file I/O, matching every other
``services`` module. ``categorize`` is used by ``services.importer`` at
import time; ``plan_reclassification``/``apply_reclassification`` back
Phase 4's bulk reclassification of existing ledger rows. Both consumers
share the same ``config/rules.toml`` rule set — see CLAUDE.md's decision
that a saved rule always applies both to future imports and to a manual
reclassification run, with no per-rule opt-out.
"""

import re
from dataclasses import dataclass
from datetime import date as date_
from decimal import Decimal

from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern``, failing loudly on an invalid regex.

    CLAUDE.md's "regex rules must fail loudly" invariant: an unusable
    pattern raises here rather than silently matching nothing. Public
    (unlike the rest of this module's helpers) so the rule-management
    router can run the same check at save time, before a bad pattern
    ever reaches ``config/rules.toml``.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid rule pattern '{pattern}': {exc}") from exc


def _compile(rule: Rule) -> re.Pattern[str]:
    """Compile ``rule``'s pattern — see ``compile_pattern``."""
    return compile_pattern(rule.pattern)


def _amount_matches(rule: Rule, magnitude: Decimal) -> bool:
    """Return whether ``magnitude`` (``abs(amount)``) is within the rule's bounds.

    Both bounds are inclusive and either may be unset (``None``, "no
    bound" — see ``app.models.rule``). A rule with neither set always
    matches on amount, i.e. amount-filtering is opt-in per rule.
    """
    if rule.min_amount is not None and magnitude < rule.min_amount:
        return False
    if rule.max_amount is not None and magnitude > rule.max_amount:
        return False
    return True


def categorize(description: str, amount: Decimal, rules: list[Rule]) -> tuple[str, str]:
    """Return the ``(category, subcategory)`` of the first matching rule.

    Only ``field == "description"`` rules are evaluated (see
    ``app.models.rule``'s note on other field values, not yet
    implemented). A rule additionally needs ``amount``'s magnitude
    (``abs``, so callers pass the ledger's signed amount directly rather
    than pre-``abs``ing it themselves) to fall within its
    ``min_amount``/``max_amount`` bounds, if it sets either — this is
    what lets one description pattern split into different rules by
    amount (e.g. a gas station chain that also sells groceries/car
    washes: a big fill-up vs. a small in-store purchase). Candidates are
    tried highest ``priority`` first, with ties keeping their original
    relative order. Returns ``("", "")`` when no rule matches or none
    exist — the caller decides the fallback (see
    ``services.importer.DEFAULT_CATEGORY``).
    """
    candidates = sorted(
        (rule for rule in rules if rule.field == "description"),
        key=lambda rule: rule.priority,
        reverse=True,
    )
    magnitude = abs(amount)
    for rule in candidates:
        if not _amount_matches(rule, magnitude):
            continue
        if _compile(rule).search(description):
            return rule.category, rule.subcategory
    return "", ""


@dataclass
class ReclassificationChange:
    """One existing ledger row a rule run would recategorize, old vs. new.

    Carries every field the preview table needs to display plus what
    ``apply_reclassification`` needs to write — the two always reuse the
    exact same list of these (see ``apply_reclassification``'s
    docstring) so preview output can never disagree with what apply
    actually does.
    """

    transaction_id: str
    date: date_
    account_id: str
    description: str
    old_category: str
    old_subcategory: str
    new_category: str
    new_subcategory: str


def plan_reclassification(
    transactions: list[Transaction], rules: list[Rule]
) -> list[ReclassificationChange]:
    """Return the rows a run of ``rules`` would recategorize, unapplied.

    Skips transfers — they carry a fixed ``"Transfer"`` category outside
    the managed category tree (see CLAUDE.md), never a rule target. Skips
    any row no rule matches (``categorize`` returns ``("", "")``): unlike
    import-time categorization's ``DEFAULT_CATEGORY`` fallback, a
    non-match here must never blank out a row's existing category. A row
    already carrying the category/subcategory a rule would assign isn't
    a change either.
    """
    changes = []
    for txn in transactions:
        if txn.type is TransactionType.TRANSFER:
            continue
        category, subcategory = categorize(txn.description, txn.amount, rules)
        if not category:
            continue
        if category == txn.category and subcategory == txn.subcategory:
            continue
        changes.append(
            ReclassificationChange(
                transaction_id=txn.id,
                date=txn.date,
                account_id=txn.account_id,
                description=txn.description,
                old_category=txn.category,
                old_subcategory=txn.subcategory,
                new_category=category,
                new_subcategory=subcategory,
            )
        )
    return changes


def apply_reclassification(
    transactions: list[Transaction], changes: list[ReclassificationChange]
) -> list[Transaction]:
    """Return ``transactions`` with every ``changes`` row's category updated.

    Pure — the caller writes the result back via storage. Takes the
    exact ``changes`` list ``plan_reclassification`` produced rather than
    recomputing it, so a preview a user already confirmed can't end up
    disagreeing with what apply actually writes. A ``transaction_id``
    with no matching row (deleted since the preview was shown) is
    silently skipped rather than erroring.
    """
    new_values = {
        change.transaction_id: (change.new_category, change.new_subcategory)
        for change in changes
    }
    result = []
    for txn in transactions:
        if txn.id not in new_values:
            result.append(txn)
            continue
        category, subcategory = new_values[txn.id]
        result.append(
            txn.model_copy(update={"category": category, "subcategory": subcategory})
        )
    return result
