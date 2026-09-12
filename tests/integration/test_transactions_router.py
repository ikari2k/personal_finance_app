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

    form_response = client.get("/transactions/new/expense")

    assert "BrandNewCategory" in form_response.text
    assert "BrandNewSub" in form_response.text


def test_expense_category_does_not_leak_into_income_form(client):
    _create_account(client)

    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "x",
            "amount": "1.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "x",
            "amount": "100.00",
            "notes": "",
        },
    )

    income_form = client.get("/transactions/new/income")
    expense_form = client.get("/transactions/new/expense")

    assert "Groceries" not in income_form.text
    assert "Salary" not in expense_form.text
    assert "Salary" in income_form.text
    assert "Groceries" in expense_form.text


def test_new_transaction_form_rejects_transfer_type(client):
    response = client.get("/transactions/new/transfer")

    assert response.status_code == 404


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


def test_account_filter_shows_only_that_accounts_transactions(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "checking purchase",
            "amount": "10.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "sav",
            "date": "2026-01-16",
            "type": "income",
            "category": "Interest",
            "subcategory": "",
            "description": "savings interest",
            "amount": "5.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?account_id=chk")

    assert "checking purchase" in filtered.text
    assert "savings interest" not in filtered.text


def test_account_filter_all_accounts_shows_everything(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "checking purchase",
            "amount": "10.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "sav",
            "date": "2026-01-16",
            "type": "income",
            "category": "Interest",
            "subcategory": "",
            "description": "savings interest",
            "amount": "5.00",
            "notes": "",
        },
    )

    unfiltered = client.get("/transactions?account_id=")

    assert "checking purchase" in unfiltered.text
    assert "savings interest" in unfiltered.text
