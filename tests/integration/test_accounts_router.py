"""Integration tests for the accounts router."""

from app import config
from app.storage.accounts import read_accounts


def test_list_accounts_empty(client):
    response = client.get("/accounts")

    assert response.status_code == 200
    assert "No accounts yet" in response.text


def test_create_account_appears_in_table(client):
    response = client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "Main account",
            "starting_balance": "1000.00",
        },
    )

    assert response.status_code == 200
    assert "Checking" in response.text
    assert "1000.00" in response.text


def test_create_account_rejects_duplicate_id(client):
    payload = {
        "account_id": "chk",
        "name": "Checking",
        "number": "1234",
        "description": "",
        "starting_balance": "0",
    }
    client.post("/accounts", data=payload)

    response = client.post("/accounts", data=payload)

    assert response.status_code == 200
    assert "already exists" in response.text


def test_edit_account_updates_fields(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    response = client.post(
        "/accounts/chk",
        data={
            "name": "Primary Checking",
            "number": "9999",
            "description": "renamed",
            "starting_balance": "50.00",
        },
    )

    assert response.status_code == 200
    assert "Primary Checking" in response.text
    assert "50.00" in response.text


def test_create_account_with_a_type_shows_its_label_in_the_table(client):
    response = client.post(
        "/accounts",
        data={
            "account_id": "cc",
            "name": "Rewards Card",
            "number": "9012",
            "description": "",
            "starting_balance": "-320.15",
            "account_type": "credit_card",
        },
    )

    assert response.status_code == 200
    assert "Credit Card" in response.text


def test_create_account_defaults_type_to_other_when_omitted(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    [account] = read_accounts(config.ACCOUNTS_PATH)
    assert account.account_type.value == "other"


def test_edit_account_changes_its_type(client):
    client.post(
        "/accounts",
        data={
            "account_id": "sav",
            "name": "Savings",
            "number": "5678",
            "description": "",
            "starting_balance": "0",
            "account_type": "checking",
        },
    )

    response = client.post(
        "/accounts/sav",
        data={
            "name": "Savings",
            "number": "5678",
            "description": "",
            "starting_balance": "0",
            "account_type": "savings",
        },
    )

    assert response.status_code == 200
    [account] = read_accounts(config.ACCOUNTS_PATH)
    assert account.account_type.value == "savings"


def test_create_account_rejects_an_invalid_type(client):
    response = client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
            "account_type": "not-a-real-type",
        },
    )

    assert response.status_code == 200
    assert "not a valid" in response.text.lower() or "invalid" in response.text.lower()


def test_create_account_defaults_status_to_active(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    [account] = read_accounts(config.ACCOUNTS_PATH)
    assert account.status.value == "active"


def test_create_account_with_closed_status_shows_its_label(client):
    response = client.post(
        "/accounts",
        data={
            "account_id": "old",
            "name": "Old Account",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
            "status": "closed",
        },
    )

    assert response.status_code == 200
    assert "Closed" in response.text


def test_closed_accounts_are_hidden_by_default_behind_a_toggle(client):
    client.post(
        "/accounts",
        data={
            "account_id": "old",
            "name": "Old Account",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
            "status": "closed",
        },
    )

    response = client.get("/accounts")

    assert response.status_code == 200
    assert "hide-closed" in response.text
    assert "Show 1 closed account" in response.text


def test_closed_accounts_toggle_pluralizes_the_count(client):
    for account_id in ("old1", "old2"):
        client.post(
            "/accounts",
            data={
                "account_id": account_id,
                "name": account_id,
                "number": "1234",
                "description": "",
                "starting_balance": "0",
                "status": "closed",
            },
        )

    response = client.get("/accounts")

    assert response.status_code == 200
    assert "Show 2 closed accounts" in response.text


def test_no_closed_accounts_toggle_when_none_are_closed(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    response = client.get("/accounts")

    assert response.status_code == 200
    assert "toggle-closed-accounts" not in response.text


def test_edit_account_closes_it(client):
    client.post(
        "/accounts",
        data={
            "account_id": "biz",
            "name": "Business",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    response = client.post(
        "/accounts/biz",
        data={
            "name": "Business",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
            "status": "closed",
        },
    )

    assert response.status_code == 200
    [account] = read_accounts(config.ACCOUNTS_PATH)
    assert account.status.value == "closed"


def test_create_account_rejects_an_invalid_status(client):
    response = client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
            "status": "not-a-real-status",
        },
    )

    assert response.status_code == 200
    assert "not a valid" in response.text.lower() or "invalid" in response.text.lower()


def test_delete_account_removes_it(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    response = client.post("/accounts/chk/delete")

    assert response.status_code == 200
    assert "Checking" not in response.text
    assert "No accounts yet" in response.text


def test_account_row_links_to_its_filtered_transactions(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )

    response = client.get("/accounts")

    assert "/transactions?account_id=chk" in response.text


def test_accounts_table_shows_starting_balance_alongside_current_balance(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "500.00",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-01",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "milk",
            "amount": "50.00",
            "notes": "",
        },
    )

    response = client.get("/accounts")

    assert "500.00" in response.text
    assert "450.00" in response.text


def test_delete_account_with_transactions_is_rejected(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "1234",
            "description": "",
            "starting_balance": "0",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-01",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "milk",
            "amount": "5.00",
            "notes": "",
        },
    )

    response = client.post("/accounts/chk/delete")

    assert response.status_code == 200
    assert "cannot be deleted" in response.text
    assert "Checking" in response.text
