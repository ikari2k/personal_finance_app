"""Integration tests for the categories router."""

from app import config
from app.storage.categories import read_categories


def test_list_categories_empty(client):
    response = client.get("/categories")

    assert response.status_code == 200
    assert "No categories yet" in response.text


def test_create_category_appears_in_tree(client):
    response = client.post(
        "/categories/expense", data={"name": "Groceries", "icon": "cart"}
    )

    assert response.status_code == 200
    assert "Groceries" in response.text
    assert read_categories(config.CATEGORIES_PATH) == {
        "income": {},
        "expense": {"Groceries": {"icon": "cart", "budget": "", "subcategories": {}}},
    }


def test_create_category_accepts_budget(client):
    response = client.post(
        "/categories/expense", data={"name": "Groceries", "budget": "400.00"}
    )

    assert response.status_code == 200
    result = read_categories(config.CATEGORIES_PATH)
    assert result["expense"]["Groceries"]["budget"] == "400.00"


def test_create_category_rejects_budget_for_income(client):
    response = client.post(
        "/categories/income", data={"name": "Salary", "budget": "100"}
    )

    assert response.status_code == 200
    assert "cannot have a monthly budget" in response.text
    assert "Salary" not in read_categories(config.CATEGORIES_PATH)["income"]


def test_new_income_category_form_hides_budget_field(client):
    response = client.get("/categories/income/new")

    assert response.status_code == 200
    assert "Monthly budget" not in response.text


def test_new_expense_category_form_shows_budget_field(client):
    response = client.get("/categories/expense/new")

    assert response.status_code == 200
    assert "Monthly budget" in response.text


def test_category_list_flags_when_subcategory_budgets_exceed_category(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "budget": "50"},
    )
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Farmers Market", "budget": "75"},
    )

    response = client.get("/categories")

    assert response.status_code == 200
    assert "budget-pill warning" in response.text


def test_category_list_does_not_flag_when_subcategory_budgets_fit(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "budget": "50"},
    )

    response = client.get("/categories")

    assert response.status_code == 200
    assert "budget-pill warning" not in response.text


def test_create_category_rejects_duplicate_name(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": ""})

    response = client.post(
        "/categories/expense", data={"name": "Groceries", "icon": ""}
    )

    assert response.status_code == 200
    assert "already exists" in response.text


def test_create_category_rejects_unknown_icon(client):
    response = client.post(
        "/categories/expense", data={"name": "Groceries", "icon": "not-a-real-icon"}
    )

    assert response.status_code == 200
    assert "unknown icon" in response.text


def test_create_category_rejects_transfer_type(client):
    response = client.post(
        "/categories/transfer", data={"name": "Groceries", "icon": ""}
    )

    assert response.status_code == 200
    assert "managed category tree" in response.text


def test_edit_category_form_is_prefilled(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})

    response = client.get("/categories/expense/Groceries/edit")

    assert response.status_code == 200
    assert 'value="Groceries"' in response.text
    assert "Save changes" in response.text


def test_update_category_renames_and_reicons(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})

    response = client.post(
        "/categories/expense/Groceries", data={"name": "Food", "icon": "utensils"}
    )

    assert response.status_code == 200
    result = read_categories(config.CATEGORIES_PATH)
    assert "Groceries" not in result["expense"]
    assert result["expense"]["Food"]["icon"] == "utensils"


def test_delete_category_removes_it(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": ""})

    response = client.post("/categories/expense/Groceries/delete")

    assert response.status_code == 200
    assert read_categories(config.CATEGORIES_PATH) == {"income": {}, "expense": {}}


def test_add_subcategory_appears_under_category(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})

    response = client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "icon": "store"},
    )

    assert response.status_code == 200
    assert "Supermarket" in response.text
    result = read_categories(config.CATEGORIES_PATH)
    assert result["expense"]["Groceries"]["subcategories"] == {
        "Supermarket": {"icon": "store", "budget": ""}
    }


def test_add_subcategory_accepts_budget(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})

    response = client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "budget": "150"},
    )

    assert response.status_code == 200
    result = read_categories(config.CATEGORIES_PATH)
    assert (
        result["expense"]["Groceries"]["subcategories"]["Supermarket"]["budget"]
        == "150"
    )


def test_add_subcategory_rejects_unknown_category(client):
    response = client.post(
        "/categories/expense/Ghost/subcategories", data={"name": "Sub", "icon": ""}
    )

    assert response.status_code == 200
    assert "no category" in response.text


def test_update_subcategory_renames_and_reicons(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "icon": ""},
    )

    response = client.post(
        "/categories/expense/Groceries/subcategories/Supermarket",
        data={"name": "Store", "icon": "store"},
    )

    assert response.status_code == 200
    result = read_categories(config.CATEGORIES_PATH)
    assert result["expense"]["Groceries"]["subcategories"] == {
        "Store": {"icon": "store", "budget": ""}
    }


def test_delete_subcategory_removes_it(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "icon": ""},
    )

    response = client.post(
        "/categories/expense/Groceries/subcategories/Supermarket/delete"
    )

    assert response.status_code == 200
    result = read_categories(config.CATEGORIES_PATH)
    assert result["expense"]["Groceries"]["subcategories"] == {}


def test_delete_category_does_not_check_ledger_usage(client):
    """Deleting a category never touches, or is blocked by, existing rows."""
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Checking",
            "number": "",
            "description": "",
            "starting_balance": "0",
        },
    )
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})
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

    response = client.post("/categories/expense/Groceries/delete")

    assert response.status_code == 200
    assert read_categories(config.CATEGORIES_PATH) == {"income": {}, "expense": {}}
