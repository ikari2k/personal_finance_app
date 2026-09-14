"""Shared fixtures for router integration tests."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose storage paths are redirected to ``tmp_path``.

    Every ``app.storage.*`` read/write function resolves its default path
    from ``app.config`` at call time (not at import time), so patching
    every path attribute here is enough to keep router tests from ever
    touching the real project's ``data/``/``config/`` files. Add a new
    line here whenever a new ``storage`` module gains its own
    ``config.*_PATH``/``*_DIR`` default — missing one silently lets
    tests read/write the real project files instead of ``tmp_path``.
    """
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.csv")
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.toml")
    monkeypatch.setattr(config, "CATEGORIES_PATH", tmp_path / "categories.toml")
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.toml")
    monkeypatch.setattr(config, "IMPORT_MAPPINGS_DIR", tmp_path / "import_mappings")
    monkeypatch.setattr(config, "IMPORT_HISTORY_PATH", tmp_path / "import_history.toml")
    with TestClient(app) as test_client:
        yield test_client
