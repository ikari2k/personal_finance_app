"""Read/write access to ``config/rules.toml``.

Shared between import-time auto-categorization (``services.categorizer``)
and the Phase 4 bulk-reclassification rule-management UI.

``min_amount``/``max_amount`` are quoted TOML strings, not bare floats —
same reasoning as ``Account.starting_balance`` (see that module's
docstring): ``tomli_w`` would otherwise serialize a ``Decimal`` as an
imprecise TOML float literal, and ``tomllib`` reads it back as a lossy
Python ``float``. ``type`` is a quoted string too (its own enum value,
e.g. ``"income"``), for consistency with the same "empty string means
unset" convention, even though it isn't a float-precision risk itself.
An empty string means "no bound"/"no type filter", parsed back to
``None`` rather than raising trying to build a ``Decimal``/
``TransactionType`` from "" — this is also why rules can't just
round-trip through ``Rule(**entry)``/``rule.model_dump()`` directly the
way simpler models can.
"""

import tomllib
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import tomli_w

from app import config
from app.models.rule import Rule
from app.models.transaction import TransactionType
from app.storage.lock import file_lock


def _to_dict(rule: Rule) -> dict[str, str | int]:
    """Convert a ``Rule`` into a TOML-serializable dict."""
    return {
        "pattern": rule.pattern,
        "field": rule.field,
        "category": rule.category,
        "subcategory": rule.subcategory,
        "priority": rule.priority,
        "min_amount": str(rule.min_amount) if rule.min_amount is not None else "",
        "max_amount": str(rule.max_amount) if rule.max_amount is not None else "",
        "type": rule.type.value if rule.type is not None else "",
    }


def _from_dict(entry: dict) -> Rule:
    """Build a ``Rule`` from one TOML table, parsing its amount bounds and type.

    ``entry.get(..., "")`` covers a rules.toml written before
    ``min_amount``/``max_amount``/``type`` existed — a missing key means
    "no bound"/"no type filter", same as an explicit empty string.
    """
    min_amount = entry.get("min_amount", "")
    max_amount = entry.get("max_amount", "")
    txn_type = entry.get("type", "")
    return Rule(
        pattern=entry["pattern"],
        field=entry.get("field", "description"),
        category=entry["category"],
        subcategory=entry.get("subcategory", ""),
        priority=entry.get("priority", 0),
        min_amount=Decimal(min_amount) if min_amount else None,
        max_amount=Decimal(max_amount) if max_amount else None,
        type=TransactionType(txn_type) if txn_type else None,
    )


def read_rules(path: Path | None = None) -> list[Rule]:
    """Read every rule from the TOML file at ``path``.

    Defaults to ``app.config.RULES_PATH``, resolved at call time (not at
    import time) so tests can redirect it via ``monkeypatch``. Returns an
    empty list if the file does not exist yet.
    """
    path = path if path is not None else config.RULES_PATH
    if not path.exists():
        return []
    with path.open("rb") as rules_file:
        data = tomllib.load(rules_file)
    return [_from_dict(entry) for entry in data.get("rules", [])]


def write_rules(rules: Iterable[Rule], path: Path | None = None) -> None:
    """Overwrite the rules TOML file at ``path`` with ``rules``.

    Defaults to ``app.config.RULES_PATH``, resolved at call time. Acquires
    a PID-based lock on ``path`` for the duration of the write and writes
    via a temp file + atomic rename.
    """
    path = path if path is not None else config.RULES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {"rules": [_to_dict(rule) for rule in rules]}
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
