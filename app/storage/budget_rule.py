"""Read/write access to ``config/budget_rule.toml``.

Holds the editable 50/30/20 target percentages as a flat table
(``needs``, ``wants``, ``savings``).
"""

import tomllib
from pathlib import Path

import tomli_w

from app import config
from app.models.budget_rule import BudgetRuleTargets
from app.storage.lock import file_lock


def read_targets(path: Path | None = None) -> BudgetRuleTargets:
    """Read the target percentages from the TOML file at ``path``.

    Defaults to ``app.config.BUDGET_RULE_PATH``, resolved at call time so
    tests can redirect it via ``monkeypatch``. Returns the 50/30/20
    defaults if the file does not exist; missing keys fall back likewise.
    """
    path = path if path is not None else config.BUDGET_RULE_PATH
    if not path.exists():
        return BudgetRuleTargets()
    with path.open("rb") as rule_file:
        data = tomllib.load(rule_file)
    return BudgetRuleTargets(**data)


def write_targets(targets: BudgetRuleTargets, path: Path | None = None) -> None:
    """Overwrite the budget rule TOML file at ``path`` with ``targets``.

    Defaults to ``app.config.BUDGET_RULE_PATH``, resolved at call time.
    Locks ``path`` for the write and replaces it atomically via a temp file.
    """
    path = path if path is not None else config.BUDGET_RULE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(targets.model_dump(), tmp_file)
        tmp_path.replace(path)
