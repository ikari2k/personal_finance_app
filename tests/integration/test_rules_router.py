"""Integration tests for the rules router (CRUD + bulk reclassification)."""

from datetime import date
from decimal import Decimal

from app import config
from app.models.transaction import Transaction, TransactionType
from app.storage.categories import read_categories
from app.storage.ledger import read_ledger, write_ledger
from app.storage.rules import read_rules


def _txn(
    id: str,
    description: str,
    category: str,
    subcategory: str = "",
    type: TransactionType = TransactionType.EXPENSE,
) -> Transaction:
    return Transaction(
        id=id,
        date=date(2026, 1, 1),
        account_id="chk",
        category=category,
        subcategory=subcategory,
        description=description,
        amount=Decimal("-10.00"),
        type=type,
    )


def test_list_rules_empty(client):
    response = client.get("/rules")

    assert response.status_code == 200
    assert "No rules yet" in response.text


def test_create_rule_appears_in_table_and_is_persisted(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ZABKA",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "priority": "5",
        },
    )

    assert response.status_code == 200
    assert "ZABKA" in response.text
    rules = read_rules(config.RULES_PATH)
    assert len(rules) == 1
    assert rules[0].pattern == "ZABKA"
    assert rules[0].category == "Groceries"
    assert rules[0].subcategory == "Supermarket"
    assert rules[0].priority == 5


def test_create_rule_rejects_invalid_regex(client):
    response = client.post(
        "/rules", data={"pattern": "[", "category": "Groceries", "priority": "0"}
    )

    assert response.status_code == 200
    assert "invalid rule pattern" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_blank_category(client):
    response = client.post(
        "/rules", data={"pattern": "ZABKA", "category": "  ", "priority": "0"}
    )

    assert response.status_code == 200
    assert "category is required" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_non_numeric_priority(client):
    response = client.post(
        "/rules",
        data={"pattern": "ZABKA", "category": "Groceries", "priority": "high"},
    )

    assert response.status_code == 200
    assert "priority must be a whole number" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_with_amount_bounds_is_persisted(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "min_amount": "100",
        },
    )

    assert response.status_code == 200
    [rule] = read_rules(config.RULES_PATH)
    assert rule.min_amount == Decimal("100")
    assert rule.max_amount is None


def test_create_rule_rejects_non_numeric_amount_bound(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "min_amount": "abc",
        },
    )

    assert response.status_code == 200
    assert "invalid minimum amount" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_negative_amount_bound(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "min_amount": "-5",
        },
    )

    assert response.status_code == 200
    assert "cannot be negative" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_min_amount_greater_than_max_amount(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "min_amount": "100",
            "max_amount": "50",
        },
    )

    assert response.status_code == 200
    assert "minimum amount cannot be greater than maximum amount" in response.text


def test_create_rule_with_exact_amount_sets_min_and_max(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "exact_amount": "123.45",
        },
    )

    assert response.status_code == 200
    [rule] = read_rules(config.RULES_PATH)
    assert rule.min_amount == Decimal("123.45")
    assert rule.max_amount == Decimal("123.45")


def test_create_rule_rejects_exact_amount_combined_with_min_amount(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "exact_amount": "100",
            "min_amount": "50",
        },
    )

    assert response.status_code == 200
    assert (
        "exact amount cannot be combined with minimum/maximum amount" in response.text
    )
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_invalid_exact_amount(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "exact_amount": "abc",
        },
    )

    assert response.status_code == 200
    assert "invalid exact amount" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_edit_rule_form_shows_exact_amount_field_for_a_min_equals_max_rule(client):
    client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "exact_amount": "100.00",
        },
    )

    response = client.get("/rules/0/edit")

    assert response.status_code == 200
    assert 'name="exact_amount" value="100.00"' in response.text


def test_rules_table_shows_exact_amount_as_equals(client):
    client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "exact_amount": "100.00",
        },
    )

    response = client.get("/rules")

    assert response.status_code == 200
    assert "= 100.00" in response.text


def test_create_rule_with_type_is_persisted(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "type": "expense",
        },
    )

    assert response.status_code == 200
    [rule] = read_rules(config.RULES_PATH)
    assert rule.type == TransactionType.EXPENSE


def test_create_rule_with_blank_type_matches_either(client):
    response = client.post(
        "/rules", data={"pattern": "ORLEN", "category": "Fuel", "priority": "0"}
    )

    assert response.status_code == 200
    [rule] = read_rules(config.RULES_PATH)
    assert rule.type is None


def test_create_rule_rejects_transfer_type(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "type": "transfer",
        },
    )

    assert response.status_code == 200
    assert "type cannot be transfer" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_create_rule_rejects_unrecognized_type(client):
    response = client.post(
        "/rules",
        data={
            "pattern": "ORLEN",
            "category": "Fuel",
            "priority": "0",
            "type": "bogus",
        },
    )

    assert response.status_code == 200
    assert "invalid type" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_edit_rule_form_is_prefilled(client):
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )

    response = client.get("/rules/0/edit")

    assert response.status_code == 200
    assert 'value="ZABKA"' in response.text
    assert 'value="Groceries"' in response.text


def test_edit_rule_missing_index_returns_404(client):
    response = client.get("/rules/0/edit")

    assert response.status_code == 404


def test_update_rule_changes_persisted_values(client):
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )

    response = client.post(
        "/rules/0",
        data={"pattern": "AUCHAN", "category": "Groceries", "priority": "10"},
    )

    assert response.status_code == 200
    rules = read_rules(config.RULES_PATH)
    assert len(rules) == 1
    assert rules[0].pattern == "AUCHAN"
    assert rules[0].priority == 10


def test_update_rule_rejects_invalid_regex_and_keeps_original(client):
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )

    response = client.post(
        "/rules/0", data={"pattern": "[", "category": "Groceries", "priority": "0"}
    )

    assert response.status_code == 200
    assert "invalid rule pattern" in response.text
    assert read_rules(config.RULES_PATH)[0].pattern == "ZABKA"


def test_delete_rule_removes_it(client):
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )

    response = client.post("/rules/0/delete")

    assert response.status_code == 200
    assert "No rules yet" in response.text
    assert read_rules(config.RULES_PATH) == []


def test_preview_reclassification_with_no_changes(client):
    response = client.get("/rules/reclassify/preview")

    assert response.status_code == 200
    assert "No changes" in response.text


def test_preview_reclassification_shows_diff_for_matching_rows(client):
    write_ledger(
        [_txn("t1", "ZABKA Z8540", category="Uncategorized")], config.LEDGER_PATH
    )
    client.post(
        "/rules",
        data={
            "pattern": "ZABKA",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "priority": "0",
        },
    )

    response = client.get("/rules/reclassify/preview")

    assert response.status_code == 200
    assert "ZABKA Z8540" in response.text
    assert "Uncategorized" in response.text
    assert "Groceries" in response.text
    assert 'name="changes_payload"' in response.text


def test_preview_reclassification_shows_the_transaction_amount(client):
    write_ledger(
        [_txn("t1", "ZABKA Z8540", category="Uncategorized")], config.LEDGER_PATH
    )
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )

    response = client.get("/rules/reclassify/preview")

    assert response.status_code == 200
    assert "10.00" in response.text


def test_reclassify_respects_rule_type_filter(client):
    write_ledger(
        [
            _txn(
                "t1",
                "ZABKA REFUND",
                category="Uncategorized",
                type=TransactionType.INCOME,
            )
        ],
        config.LEDGER_PATH,
    )
    client.post(
        "/rules",
        data={
            "pattern": "ZABKA",
            "category": "Groceries",
            "priority": "0",
            "type": "expense",
        },
    )

    response = client.get("/rules/reclassify/preview")

    assert response.status_code == 200
    assert "No changes" in response.text


def test_reclassify_apply_updates_ledger_and_creates_category(client):
    write_ledger(
        [_txn("t1", "ZABKA Z8540", category="Uncategorized")], config.LEDGER_PATH
    )
    client.post(
        "/rules",
        data={
            "pattern": "ZABKA",
            "category": "Groceries",
            "subcategory": "Supermarket",
            "priority": "0",
        },
    )
    preview = client.get("/rules/reclassify/preview")
    payload = preview.text.split('name="changes_payload" value="')[1].split('"')[0]
    import html

    payload = html.unescape(payload)

    response = client.post("/rules/reclassify/apply", data={"changes_payload": payload})

    assert response.status_code == 200
    assert response.headers["hx-trigger"]
    assert "Reclassified 1 transaction" in response.headers["hx-trigger"]
    updated = read_ledger(config.LEDGER_PATH)
    assert updated[0].category == "Groceries"
    assert updated[0].subcategory == "Supermarket"
    categories = read_categories(config.CATEGORIES_PATH)
    assert "Groceries" in categories["expense"]
    assert "Supermarket" in categories["expense"]["Groceries"]["subcategories"]


def test_reclassify_apply_skips_a_row_deleted_since_preview(client):
    write_ledger(
        [_txn("t1", "ZABKA Z8540", category="Uncategorized")], config.LEDGER_PATH
    )
    client.post(
        "/rules", data={"pattern": "ZABKA", "category": "Groceries", "priority": "0"}
    )
    preview = client.get("/rules/reclassify/preview")
    payload = preview.text.split('name="changes_payload" value="')[1].split('"')[0]
    import html

    payload = html.unescape(payload)
    write_ledger([], config.LEDGER_PATH)

    response = client.post("/rules/reclassify/apply", data={"changes_payload": payload})

    assert response.status_code == 200
    assert read_ledger(config.LEDGER_PATH) == []


def test_reclassify_never_touches_transfers(client):
    write_ledger(
        [
            _txn(
                "t1",
                "Monthly savings transfer",
                category="Transfer",
                type=TransactionType.TRANSFER,
            )
        ],
        config.LEDGER_PATH,
    )
    client.post(
        "/rules", data={"pattern": ".*", "category": "Groceries", "priority": "0"}
    )

    response = client.get("/rules/reclassify/preview")

    assert "No changes" in response.text
