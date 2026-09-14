"""Domain model for one auto-categorization rule.

Shared between import-time auto-categorization (Phase 3,
``services.categorizer``) and bulk reclassification of existing ledger
rows (Phase 4's rule-management UI, not built yet) — see
``config/rules.toml`` in CLAUDE.md. Only ``field="description"`` is
interpreted by the Phase 3 categorizer today; other field values are
accepted here (Phase 4 may add matching against other row fields) but
won't match anything until that's implemented.
"""

from pydantic import BaseModel


class Rule(BaseModel):
    """One ``pattern`` → ``category``/``subcategory`` auto-categorization rule.

    ``pattern`` is a regular expression matched against ``field``
    (case-insensitively). ``priority`` breaks ties when more than one
    rule matches — higher runs first, so a more specific rule can be
    given priority over a broader catch-all.
    """

    pattern: str
    field: str = "description"
    category: str
    subcategory: str = ""
    priority: int = 0
