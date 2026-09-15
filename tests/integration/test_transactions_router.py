"""Integration tests for the transactions router."""

from decimal import Decimal

from app import config
from app.storage.ledger import read_ledger


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


def test_category_filter_shows_only_matching_category(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "expense",
            "category": "Transportation",
            "subcategory": "",
            "description": "gas station",
            "amount": "30.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?category=Groceries")

    assert "grocery run" in filtered.text
    assert "gas station" not in filtered.text


def test_subcategory_filter_shows_only_matching_subcategory(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Farmers Market",
            "description": "farmers market trip",
            "amount": "20.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?subcategory=Supermarket")

    assert "grocery run" in filtered.text
    assert "farmers market trip" not in filtered.text


def test_category_and_subcategory_filters_combine_as_and(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "expense",
            "category": "Shopping",
            "subcategory": "Supermarket",
            "description": "mislabeled row",
            "amount": "15.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?category=Groceries&subcategory=Supermarket")

    assert "grocery run" in filtered.text
    assert "mislabeled row" not in filtered.text


def test_category_and_subcategory_filters_are_sticky_via_cookies(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?category=Groceries&subcategory=Supermarket")
    assert filtered.cookies.get("category") == "Groceries"
    assert filtered.cookies.get("subcategory") == "Supermarket"

    remembered = client.get("/transactions")
    assert "grocery run" in remembered.text


def test_edit_transaction_updates_fields(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "original",
            "amount": "10.00",
            "notes": "",
        },
    )
    transaction_id = read_ledger(config.LEDGER_PATH)[0].id

    response = client.post(
        f"/transactions/{transaction_id}",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "edited",
            "amount": "25.00",
            "notes": "",
        },
    )

    assert response.status_code == 200
    ledger = read_ledger(config.LEDGER_PATH)
    assert len(ledger) == 1
    assert ledger[0].id == transaction_id
    assert ledger[0].description == "edited"
    assert ledger[0].amount == Decimal("-25.00")


def test_edit_transaction_form_rejects_a_transfer_leg(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
    client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "sav",
            "date": "2026-01-15",
            "amount": "50.00",
            "description": "",
            "notes": "",
        },
    )
    transfer_leg_id = read_ledger(config.LEDGER_PATH)[0].id

    response = client.get(f"/transactions/{transfer_leg_id}/edit")

    assert response.status_code == 400


def test_delete_transaction_removes_it(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "to delete",
            "amount": "10.00",
            "notes": "",
        },
    )
    transaction_id = read_ledger(config.LEDGER_PATH)[0].id

    response = client.post(f"/transactions/{transaction_id}/delete")

    assert response.status_code == 200
    assert read_ledger(config.LEDGER_PATH) == []


def test_delete_transfer_removes_both_legs(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
    client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "sav",
            "date": "2026-01-15",
            "amount": "50.00",
            "description": "",
            "notes": "",
        },
    )
    outflow_id = read_ledger(config.LEDGER_PATH)[0].id

    response = client.post(f"/transactions/{outflow_id}/delete")

    assert response.status_code == 200
    assert read_ledger(config.LEDGER_PATH) == []


def test_grouping_toggle_persists_across_bare_navigation(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Trader Joe's",
            "amount": "10.00",
            "notes": "",
        },
    )

    toggle = client.get("/transactions?by_month=false&by_type=true&account_id=")
    assert toggle.cookies.get("by_month") == "false"

    bare = client.get("/transactions")

    assert "Month grouping: off" in bare.text
    assert "flat-net-total" in bare.text
    assert "month-section" not in bare.text


def test_explicit_grouping_param_overrides_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Trader Joe's",
            "amount": "10.00",
            "notes": "",
        },
    )
    client.get("/transactions?by_month=false&by_type=true&account_id=")

    explicit = client.get("/transactions?by_month=true&by_type=true&account_id=")

    assert "Month grouping: on" in explicit.text
    assert "month-section" in explicit.text


def test_account_filter_persists_across_bare_navigation(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
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
    assert filtered.cookies.get("account_id") == "chk"

    bare = client.get("/transactions")

    assert "savings interest" not in bare.text


def test_explicit_empty_account_id_overrides_cookie(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
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
    client.get("/transactions?account_id=chk")

    all_accounts = client.get("/transactions?account_id=")

    assert "savings interest" in all_accounts.text


def test_post_create_refresh_uses_cookie_grouping(client):
    _create_account(client)
    client.get("/transactions?by_month=false&by_type=true&account_id=")

    response = client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Trader Joe's",
            "amount": "10.00",
            "notes": "",
        },
    )

    assert "flat-net-total" in response.text
    assert "month-section" not in response.text
