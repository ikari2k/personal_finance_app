"""Integration tests for the accounts router."""


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
