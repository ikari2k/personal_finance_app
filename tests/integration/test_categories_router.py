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
        "expense": {"Groceries": {"icon": "cart", "subcategories": {}}},
    }


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
    assert result["expense"]["Groceries"]["subcategories"] == {"Supermarket": "store"}


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
    assert result["expense"]["Groceries"]["subcategories"] == {"Store": "store"}


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
