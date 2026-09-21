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


def test_type_filter_shows_only_matching_type(client):
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
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "paycheck",
            "amount": "1000.00",
            "notes": "",
        },
    )
    client.post(
        "/transfers",
        data={
            "from_account_id": "chk",
            "to_account_id": "sav",
            "date": "2026-01-17",
            "amount": "50.00",
            "description": "moved to savings",
            "notes": "",
        },
    )

    expense_only = client.get("/transactions?txn_type=expense")
    income_only = client.get("/transactions?txn_type=income")
    transfer_only = client.get("/transactions?txn_type=transfer")

    assert "grocery run" in expense_only.text
    assert "paycheck" not in expense_only.text
    assert "moved to savings" not in expense_only.text

    assert "paycheck" in income_only.text
    assert "grocery run" not in income_only.text

    assert "moved to savings" in transfer_only.text
    assert "grocery run" not in transfer_only.text
    assert "paycheck" not in transfer_only.text


def test_type_filter_is_sticky_via_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?txn_type=expense")
    assert filtered.cookies.get("txn_type") == "expense"

    remembered = client.get("/transactions")
    assert "grocery run" in remembered.text


def test_explicit_empty_txn_type_overrides_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "paycheck",
            "amount": "1000.00",
            "notes": "",
        },
    )
    client.get("/transactions?txn_type=expense")

    all_types = client.get("/transactions?txn_type=")

    assert "paycheck" in all_types.text


def test_search_filter_matches_description_case_insensitively(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Trader Joe's run",
            "amount": "40.00",
            "notes": "",
        },
    )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "paycheck",
            "amount": "1000.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?search=trader")

    assert "Trader Joe" in filtered.text
    assert "paycheck" not in filtered.text


def test_search_filter_empty_shows_everything(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )

    unfiltered = client.get("/transactions?search=")

    assert "grocery run" in unfiltered.text


def test_search_filter_combines_with_other_filters_as_and(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
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
            "subcategory": "",
            "description": "grocery mislabeled",
            "amount": "15.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?search=grocery&category=Groceries")

    assert "grocery run" in filtered.text
    assert "grocery mislabeled" not in filtered.text


def test_search_filter_is_sticky_via_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "40.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?search=grocery")
    assert filtered.cookies.get("search") == "grocery"

    remembered = client.get("/transactions")
    assert "grocery run" in remembered.text


def test_explicit_empty_search_overrides_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "paycheck",
            "amount": "1000.00",
            "notes": "",
        },
    )
    client.get("/transactions?search=grocery")

    all_rows = client.get("/transactions?search=")

    assert "paycheck" in all_rows.text


def test_date_range_filter_matches_inclusive_bounds(client):
    _create_account(client)
    for d in ("2026-01-10", "2026-01-15", "2026-01-20"):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": d,
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": d,
                "amount": "10.00",
                "notes": "",
            },
        )

    filtered = client.get("/transactions?date_from=2026-01-10&date_to=2026-01-15")

    assert "2026-01-10" in filtered.text
    assert "2026-01-15" in filtered.text
    assert "2026-01-20" not in filtered.text


def test_date_range_filter_only_from_set(client):
    _create_account(client)
    for d in ("2026-01-10", "2026-01-20"):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": d,
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": d,
                "amount": "10.00",
                "notes": "",
            },
        )

    filtered = client.get("/transactions?date_from=2026-01-15")

    assert "2026-01-10" not in filtered.text
    assert "2026-01-20" in filtered.text


def test_date_range_filter_only_to_set(client):
    _create_account(client)
    for d in ("2026-01-10", "2026-01-20"):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": d,
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": d,
                "amount": "10.00",
                "notes": "",
            },
        )

    filtered = client.get("/transactions?date_to=2026-01-15")

    assert "2026-01-10" in filtered.text
    assert "2026-01-20" not in filtered.text


def test_date_range_filter_is_sticky_via_cookies(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "10.00",
            "notes": "",
        },
    )

    filtered = client.get("/transactions?date_from=2026-01-01&date_to=2026-01-31")
    assert filtered.cookies.get("date_from") == "2026-01-01"
    assert filtered.cookies.get("date_to") == "2026-01-31"

    remembered = client.get("/transactions")
    assert "grocery run" in remembered.text


def test_explicit_empty_date_range_overrides_cookie(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "10.00",
            "notes": "",
        },
    )
    client.get("/transactions?date_from=2026-06-01&date_to=2026-06-30")

    all_rows = client.get("/transactions?date_from=&date_to=")

    assert "grocery run" in all_rows.text


def test_date_range_filter_ignores_malformed_date(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "grocery run",
            "amount": "10.00",
            "notes": "",
        },
    )

    response = client.get("/transactions?date_from=not-a-date")

    assert response.status_code == 200
    assert "grocery run" in response.text


def test_clear_filters_button_hidden_with_no_active_filter(client):
    response = client.get("/transactions")

    assert response.status_code == 200
    assert "Clear filters" not in response.text


def test_clear_filters_button_shown_when_a_filter_is_active(client):
    response = client.get("/transactions?search=coffee")

    assert response.status_code == 200
    assert "Clear filters" in response.text


def test_clear_filters_resets_every_filter_field(client):
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
            "amount": "10.00",
            "notes": "",
        },
    )
    client.get(
        "/transactions"
        "?account_id=chk&category=Groceries&subcategory=Supermarket"
        "&txn_type=expense&search=grocery"
        "&date_from=2026-01-01&date_to=2026-01-31"
    )

    cleared = client.get(
        "/transactions"
        "?account_id=&category=&subcategory=&txn_type=&search="
        "&date_from=&date_to="
    )

    assert cleared.status_code == 200
    assert "grocery run" in cleared.text
    for cookie_name in (
        "account_id",
        "category",
        "subcategory",
        "txn_type",
        "search",
        "date_from",
        "date_to",
    ):
        assert cleared.cookies.get(cookie_name) in ("", '""')
    assert "Clear filters" not in cleared.text


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
    # description is read-only once a transaction exists — see
    # test_edit_transaction_never_changes_description below — only
    # amount (and every other non-description field) changes here.
    assert ledger[0].description == "original"
    assert ledger[0].amount == Decimal("-25.00")


def test_edit_transaction_never_changes_description(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "from bank export",
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
            "description": "trying to overwrite it",
            "amount": "10.00",
            "notes": "my own note",
        },
    )

    assert response.status_code == 200
    [updated] = read_ledger(config.LEDGER_PATH)
    assert updated.description == "from bank export"
    assert updated.notes == "my own note"


def test_inline_category_update_changes_category_and_subcategory(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "original",
            "amount": "10.00",
            "notes": "",
        },
    )
    # ensure_category (via a second transaction) creates a second real
    # category/subcategory pair to switch to.
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "expense",
            "category": "Dining",
            "subcategory": "Restaurants",
            "description": "other",
            "amount": "20.00",
            "notes": "",
        },
    )
    transaction_id = next(
        t.id for t in read_ledger(config.LEDGER_PATH) if t.description == "original"
    )

    response = client.post(
        f"/transactions/{transaction_id}/category",
        data={"category": "Dining", "subcategory": "Restaurants"},
    )

    assert response.status_code == 200
    updated = next(t for t in read_ledger(config.LEDGER_PATH) if t.id == transaction_id)
    assert updated.category == "Dining"
    assert updated.subcategory == "Restaurants"
    # Every other field is untouched.
    assert updated.description == "original"
    assert updated.amount == Decimal("-10.00")


def test_inline_category_update_to_bare_category_clears_subcategory(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "description": "original",
            "amount": "10.00",
            "notes": "",
        },
    )
    transaction_id = read_ledger(config.LEDGER_PATH)[0].id

    client.post(
        f"/transactions/{transaction_id}/category",
        data={"category": "Groceries", "subcategory": ""},
    )

    updated = next(t for t in read_ledger(config.LEDGER_PATH) if t.id == transaction_id)
    assert updated.category == "Groceries"
    assert updated.subcategory == ""


def test_inline_category_update_rejects_unknown_category(client):
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
        f"/transactions/{transaction_id}/category",
        data={"category": "Not A Real Category", "subcategory": ""},
    )

    assert response.status_code == 400
    updated = next(t for t in read_ledger(config.LEDGER_PATH) if t.id == transaction_id)
    assert updated.category == "Groceries"


def test_inline_category_update_rejects_a_transfer_leg(client):
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

    response = client.post(
        f"/transactions/{transfer_leg_id}/category",
        data={"category": "Groceries", "subcategory": ""},
    )

    assert response.status_code == 404
    updated = next(
        t for t in read_ledger(config.LEDGER_PATH) if t.id == transfer_leg_id
    )
    assert updated.category == "Transfer"


def test_inline_notes_update_sets_notes_leaves_description_untouched(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Lidl 4471",
            "amount": "10.00",
            "notes": "",
        },
    )
    transaction_id = read_ledger(config.LEDGER_PATH)[0].id

    response = client.post(
        f"/transactions/{transaction_id}/notes",
        data={"notes": "weekly shop"},
    )

    assert response.status_code == 200
    assert "weekly shop" in response.text
    [updated] = read_ledger(config.LEDGER_PATH)
    assert updated.notes == "weekly shop"
    assert updated.description == "Lidl 4471"


def test_inline_notes_update_allowed_on_a_transfer_leg(client):
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

    response = client.post(
        f"/transactions/{transfer_leg_id}/notes",
        data={"notes": "moving to savings"},
    )

    assert response.status_code == 200
    updated = next(
        t for t in read_ledger(config.LEDGER_PATH) if t.id == transfer_leg_id
    )
    assert updated.notes == "moving to savings"


def test_inline_notes_update_rejects_unknown_id(client):
    response = client.post(
        "/transactions/ghost/notes",
        data={"notes": "anything"},
    )

    assert response.status_code == 404


def test_transactions_list_shows_notes_instead_of_description_when_set(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Lidl 4471",
            "amount": "10.00",
            "notes": "weekly shop",
        },
    )

    response = client.get("/transactions")

    assert response.status_code == 200
    assert 'value="weekly shop"' in response.text
    assert 'value="Lidl 4471"' not in response.text
    # The original description stays reachable via a hover tooltip even
    # though it's no longer the visible value.
    assert 'title="Original description: Lidl 4471"' in response.text


def test_transactions_list_falls_back_to_description_when_no_notes(client):
    _create_account(client)
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Groceries",
            "subcategory": "",
            "description": "Lidl 4471",
            "amount": "10.00",
            "notes": "",
        },
    )

    response = client.get("/transactions")

    assert response.status_code == 200
    assert "Lidl 4471" in response.text


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


def test_month_summary_shows_transaction_count(client):
    _create_account(client)
    for i in range(3):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": "2026-01-15",
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": f"item {i}",
                "amount": "10.00",
                "notes": "",
            },
        )

    response = client.get("/transactions")

    assert "(3)" in response.text


def test_type_group_header_shows_transaction_count(client):
    _create_account(client)
    for i in range(2):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": "2026-01-15",
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": f"item {i}",
                "amount": "10.00",
                "notes": "",
            },
        )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-16",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "paycheck",
            "amount": "1000.00",
            "notes": "",
        },
    )

    response = client.get("/transactions?by_month=true&by_type=true")

    assert "Expense" in response.text
    assert "(2)" in response.text
    assert "Income" in response.text
    assert "(1)" in response.text


def test_flat_net_total_shows_transaction_count(client):
    _create_account(client)
    for i in range(4):
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": "2026-01-15",
                "type": "expense",
                "category": "Groceries",
                "subcategory": "",
                "description": f"item {i}",
                "amount": "10.00",
                "notes": "",
            },
        )

    response = client.get("/transactions?by_month=false")

    assert "Net total" in response.text
    assert "(4)" in response.text
