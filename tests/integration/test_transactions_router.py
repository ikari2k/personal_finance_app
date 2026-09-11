"""Integration tests for the transactions router."""


def _create_account(client, account_id="chk", starting_balance="1000.00"):
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


def test_create_transaction_appears_in_table(client):
    _create_account(client)

    response = client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "Trader Joe's",
            "amount": "42.17",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert "Trader Joe&#39;s" in response.text or "Trader Joe's" in response.text
    assert "42.17" in response.text


def test_create_transaction_registers_new_category_on_the_fly(client):
    _create_account(client)

    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "BrandNewCategory",
            "subcategory": "BrandNewSub",
            "description": "x",
            "amount": "1.00",
            "notes": "",
        },
    )

    form_response = client.get("/transactions/new")

    assert "BrandNewCategory" in form_response.text
    assert "BrandNewSub" in form_response.text


def test_create_transaction_rejects_unknown_account(client):
    response = client.post(
        "/transactions",
        data={
            "account_id": "ghost",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "x",
            "amount": "1.00",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert "unknown account_id" in response.text


def test_account_balance_reflects_starting_balance_plus_transactions(client):
    _create_account(client, starting_balance="1000.00")
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "x",
            "amount": "50.00",
            "notes": "",
        },
    )

    response = client.get("/accounts")

    assert "950.00" in response.text
