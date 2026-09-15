"""Domain model for one auto-categorization rule.

Shared between import-time auto-categorization (Phase 3,
``services.categorizer``) and bulk reclassification of existing ledger
rows (Phase 4's rule-management UI, not built yet) — see
``config/rules.toml`` in CLAUDE.md. Only ``field="description"`` is
interpreted by the Phase 3 categorizer today; other field values are
accepted here (Phase 4 may add matching against other row fields) but
won't match anything until that's implemented.
"""

from decimal import Decimal

from pydantic import BaseModel

from app.models.transaction import TransactionType


class Rule(BaseModel):
    """One ``pattern`` → ``category``/``subcategory`` auto-categorization rule.

    ``pattern`` is a regular expression matched against ``field``
    (case-insensitively). ``priority`` breaks ties when more than one
    rule matches — higher runs first, so a more specific rule can be
    given priority over a broader catch-all.

    ``min_amount``/``max_amount`` (either or both may be ``None``, "no
    bound") narrow a match to transactions whose *magnitude*
    (``abs(amount)``) falls within them, inclusive on both ends — e.g. a
    gas-station chain that also sells groceries/car washes might need two
    rules sharing one ``pattern`` but split by amount, one with
    ``min_amount=100`` (the big fuel fill-ups) and one with
    ``max_amount=99.99`` (everything smaller). A magnitude alone can't
    tell an expense from an income of the same size, though (a 150
    outflow and a 150 refund both have ``abs(amount) == 150``) — that's
    what ``type`` is for: ``None`` (the default) matches either income or
    expense, or it can be pinned to just one. Never ``TRANSFER``: rules
    never apply to transfers in the first place (see
    ``services.categorizer.plan_reclassification``), so that value is
    accepted by the type system but meaningless here — the rule-management
    UI never offers it as a choice. Both ``min_amount``/``max_amount`` and
    ``type`` are stored in ``config/rules.toml`` as quoted strings, same
    convention as ``Account.starting_balance`` — see ``app.storage.rules``'s
    module docstring for why (``tomli_w`` would otherwise serialize a bare
    ``Decimal`` as an imprecise TOML float; ``type`` piggybacks on the same
    "empty string means unset" convention for consistency, even though it
    isn't itself a lossy-float risk).
    """

    pattern: str
    field: str = "description"
    category: str
    subcategory: str = ""
    priority: int = 0
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    type: TransactionType | None = None
