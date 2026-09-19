"""Business rules for creating/editing auto-categorization rules.

Pure functions over rule form fields — no file I/O, matching every other
``services`` module. Callers (``app.routers.rules``) parse/validate a
submitted form via ``validate_rule`` here, then build and persist the
``Rule`` themselves via ``app.storage.rules``.
"""

from decimal import Decimal, InvalidOperation

from app.models.transaction import TransactionType
from app.services.categorizer import compile_pattern


def parse_priority(raw: str) -> int:
    """Parse a raw priority form field; blank means ``0``."""
    raw = raw.strip()
    try:
        return int(raw) if raw else 0
    except ValueError as exc:
        raise ValueError("priority must be a whole number") from exc


def parse_amount_bound(raw: str, field_name: str) -> Decimal | None:
    """Parse a raw min/max-amount form field; blank means "no bound".

    Raises ``ValueError`` (not ``InvalidOperation``) on unparseable
    input, so callers can fold it into the same ``except ValueError``
    block that already handles every other rule-form validation error —
    same pattern as ``services.categories._parse_budget``.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field_name}") from exc
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return value


def parse_rule_type(raw: str) -> TransactionType | None:
    """Parse the rule form's Type select; blank means "either income or expense".

    The select only ever offers "", "income", or "expense", but this
    still validates rather than trusting the client: any other value
    (including "transfer" — a rule pinned to it would be permanently
    unreachable, since rules never run against transfers in the first
    place, see ``app.models.rule``) raises loudly instead of silently
    saving a dead rule.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        txn_type = TransactionType(raw)
    except ValueError as exc:
        raise ValueError(f"invalid type '{raw}'") from exc
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("type cannot be transfer — rules never match transfers")
    return txn_type


def validate_rule(
    pattern: str,
    category: str,
    priority: str,
    min_amount: str,
    max_amount: str,
    exact_amount: str,
    txn_type: str,
) -> tuple[int, Decimal | None, Decimal | None, TransactionType | None]:
    """Validate a submitted rule; returns parsed fields or raises ``ValueError``.

    Regex compile-checked here, at save time — CLAUDE.md's "regex rules
    must fail loudly" invariant, extended from Phase 3's apply-time-only
    check so a bad pattern never reaches ``config/rules.toml`` at all.

    ``exact_amount`` is a friendlier alternative to setting
    ``min_amount``/``max_amount`` to the same value by hand — it's not
    its own model field (``Rule`` has no ``exact_amount``), just a form
    convenience that collapses to ``min_value = max_value =
    exact_value`` here, reusing the amount-matching logic
    ``categorize``/``_amount_matches`` already has for an inclusive
    range that happens to be a single point. Combining it with a
    separate min/max is rejected as ambiguous rather than picking one
    silently.
    """
    compile_pattern(pattern)
    if not category.strip():
        raise ValueError("category is required")
    priority_value = parse_priority(priority)
    exact_value = parse_amount_bound(exact_amount, "exact amount")
    min_value = parse_amount_bound(min_amount, "minimum amount")
    max_value = parse_amount_bound(max_amount, "maximum amount")
    if exact_value is not None:
        if min_value is not None or max_value is not None:
            raise ValueError(
                "exact amount cannot be combined with minimum/maximum amount"
            )
        min_value = max_value = exact_value
    elif min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError("minimum amount cannot be greater than maximum amount")
    type_value = parse_rule_type(txn_type)
    return priority_value, min_value, max_value, type_value
