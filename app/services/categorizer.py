"""Apply auto-categorization rules to a transaction description.

Pure functions over in-memory data — no file I/O, matching every other
``services`` module. Used by ``services.importer`` at import time; the
same rules will later back Phase 4's bulk reclassification of existing
ledger rows.
"""

import re

from app.models.rule import Rule


def _compile(rule: Rule) -> re.Pattern[str]:
    """Compile ``rule``'s pattern, failing loudly on an invalid regex.

    CLAUDE.md's "regex rules must fail loudly" invariant: an unusable
    pattern raises here rather than silently matching nothing.
    """
    try:
        return re.compile(rule.pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid rule pattern '{rule.pattern}': {exc}") from exc


def categorize(description: str, rules: list[Rule]) -> tuple[str, str]:
    """Return the ``(category, subcategory)`` of the first matching rule.

    Only ``field == "description"`` rules are evaluated (see
    ``app.models.rule``'s note on other field values, not yet
    implemented). Candidates are tried highest ``priority`` first, with
    ties keeping their original relative order. Returns ``("", "")`` when
    no rule matches or none exist — the caller decides the fallback (see
    ``services.importer.DEFAULT_CATEGORY``).
    """
    candidates = sorted(
        (rule for rule in rules if rule.field == "description"),
        key=lambda rule: rule.priority,
        reverse=True,
    )
    for rule in candidates:
        if _compile(rule).search(description):
            return rule.category, rule.subcategory
    return "", ""
