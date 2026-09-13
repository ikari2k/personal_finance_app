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


def _create_transfer(client, from_id="chk", to_id="sav", amount="200.00"):
    client.post(
        "/transfers",
        data={
            "from_account_id": from_id,
            "to_account_id": to_id,
            "date": "2026-01-15",
            "amount": amount,
            "description": "Monthly savings",
            "notes": "",
        },
    )
    return read_ledger(config.LEDGER_PATH)[0].transfer_id


def test_edit_transfer_form_is_prefilled_from_both_legs(client):
    _create_account(client, "chk", "1000.00")
    _create_account(client, "sav", "500.00")
    transfer_id = _create_transfer(client)

    response = client.get(f"/transfers/{transfer_id}/edit")

    assert response.status_code == 200
    assert 'value="200.00"' in response.text
    assert "Monthly savings" in response.text
    assert f"/transfers/{transfer_id}" in response.text
    assert "Save changes" in response.text


def test_edit_transfer_form_rejects_unknown_id(client):
    response = client.get("/transfers/ghost/edit")

    assert response.status_code == 404


def test_update_transfer_changes_both_legs_and_balances(client):
    _create_account(client, "chk", "1000.00")
    _create_account(client, "sav", "500.00")
    _create_account(client, "cc", "0.00")
    transfer_id = _create_transfer(client)

    response = client.post(
        f"/transfers/{transfer_id}",
        data={
            "from_account_id": "chk",
            "to_account_id": "cc",
            "date": "2026-01-16",
            "amount": "300.00",
            "description": "renamed",
            "notes": "",
        },
    )

    assert response.status_code == 200
    transactions = read_ledger(config.LEDGER_PATH)
    assert len(transactions) == 2
    outflow = next(t for t in transactions if t.amount < 0)
    inflow = next(t for t in transactions if t.amount > 0)
    assert outflow.account_id == "chk"
    assert inflow.account_id == "cc"
    assert outflow.amount == -300
    assert inflow.amount == 300
    assert outflow.description == "renamed"
    assert outflow.transfer_id == transfer_id
    assert inflow.transfer_id == transfer_id

    accounts_page = client.get("/accounts")
    assert "700.00" in accounts_page.text  # chk: 1000 - 300
    assert "500.00" in accounts_page.text  # sav: untouched
    assert "300.00" in accounts_page.text  # cc: 0 + 300


def test_update_transfer_rejects_same_account_on_both_sides(client):
    _create_account(client, "chk", "1000.00")
    _create_account(client, "sav", "500.00")
    transfer_id = _create_transfer(client)

    response = client.post(
        f"/transfers/{transfer_id}",
        data={
            "from_account_id": "chk",
            "to_account_id": "chk",
            "date": "2026-01-15",
            "amount": "200.00",
            "description": "",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert "must be between two different accounts" in response.text
    # unchanged
    transactions = read_ledger(config.LEDGER_PATH)
    assert {t.account_id for t in transactions} == {"chk", "sav"}
