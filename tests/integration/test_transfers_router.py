"""Integration tests for the transfers router."""

from app import config
from app.storage.ledger import read_ledger


def _create_account(client, account_id, starting_balance):
    client.post(
        "/accounts",
        data={
            "account_id": account_id,
            "name": account_id.title(),
            "number": "1",
            "description": "",
            "starting_balance": starting_balance,
        },
    )


def test_transfer_creates_linked_two_row_pair(client):
    _create_account(client, "chk", "1000.00")
    _create_account(client, "sav", "500.00")

    response = client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "sav",
            "date": "2026-01-15",
            "amount": "200.00",
            "description": "Monthly savings",
            "notes": "",
        },
    )

    assert response.status_code == 200

    transactions = read_ledger(config.LEDGER_PATH)
    assert len(transactions) == 2
    outflow, inflow = transactions
    assert outflow.transfer_id == inflow.transfer_id
    assert outflow.account_id == "chk"
    assert inflow.account_id == "sav"
    assert outflow.amount == -inflow.amount


def test_transfer_updates_both_account_balances(client):
    _create_account(client, "chk", "1000.00")
    _create_account(client, "sav", "500.00")

    client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "sav",
            "date": "2026-01-15",
            "amount": "200.00",
            "description": "",
            "notes": "",
        },
    )

    response = client.get("/accounts")

    assert "800.00" in response.text
    assert "700.00" in response.text


def test_transfer_rejects_same_account_on_both_sides(client):
    _create_account(client, "chk", "1000.00")

    response = client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "chk",
            "date": "2026-01-15",
            "amount": "10.00",
            "description": "",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert "must be between two different accounts" in response.text
