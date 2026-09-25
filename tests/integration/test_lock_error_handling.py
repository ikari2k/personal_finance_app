"""Integration test for the app-wide LockError -> toast exception handler."""

import json

from app.routers import accounts as accounts_router
from app.storage.lock import LockError


def test_lock_conflict_on_write_returns_toast_not_500(client, monkeypatch):
    """A LockError raised during a write surfaces as a toast, not a 500."""

    def _raise_lock_error(_accounts):
        raise LockError("Timed out waiting for lock")

    monkeypatch.setattr(accounts_router, "write_accounts", _raise_lock_error)

    response = client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "starting_balance": "0",
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Reswap"] == "none"
    trigger = json.loads(response.headers["HX-Trigger"])
    assert "another save was in progress" in trigger["toast"].lower()
    assert "close-dialog" not in trigger
