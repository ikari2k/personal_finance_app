"""Read/write access to ``config/accounts.toml``.

``starting_balance`` is stored as a quoted TOML string, not a bare TOML
float: ``tomli_w`` serializes ``Decimal`` as a bare float literal and
``tomllib`` reads it back as a Python ``float``, which risks silent
precision loss for money. Round-tripping through a string avoids that.
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path

import tomli_w

from app import config
from app.models.account import Account
from app.storage.lock import file_lock


def _to_dict(account: Account) -> dict[str, str]:
    """Convert an ``Account`` into a TOML-serializable dict."""
    return {
        "id": account.id,
        "name": account.name,
        "number": account.number,
        "description": account.description,
        "starting_balance": str(account.starting_balance),
    }


def read_accounts(path: Path | None = None) -> list[Account]:
    """Read every account from the TOML file at ``path``.

    Defaults to ``app.config.ACCOUNTS_PATH``, resolved at call time (not
    at import time) so tests can redirect it via ``monkeypatch``. Returns
    an empty list if the file does not exist yet.
    """
    path = path if path is not None else config.ACCOUNTS_PATH
    if not path.exists():
        return []
    with path.open("rb") as accounts_file:
        data = tomllib.load(accounts_file)
    return [Account(**entry) for entry in data.get("accounts", [])]


def write_accounts(accounts: Iterable[Account], path: Path | None = None) -> None:
    """Overwrite the accounts TOML file at ``path`` with ``accounts``.

    Defaults to ``app.config.ACCOUNTS_PATH``, resolved at call time.
    Acquires a PID-based lock on ``path`` for the duration of the write and
    writes via a temp file + atomic rename.
    """
    path = path if path is not None else config.ACCOUNTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = {"accounts": [_to_dict(account) for account in accounts]}
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("wb") as tmp_file:
            tomli_w.dump(data, tmp_file)
        tmp_path.replace(path)
