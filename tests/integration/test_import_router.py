"""Integration tests for the bank CSV import wizard router."""

import html

from app import config
from app.storage.categories import read_categories
from app.storage.import_mappings import read_mapping
from app.storage.ledger import read_ledger

SAMPLE_CSV = (
    "date,description,amount,account\n"
    "2026-09-01,Coffee Shop,-3.50,1234\n"
    "2026-09-02,Paycheck,3200.00,1234\n"
    "2026-09-03,Other Account Row,-10.00,9999\n"
)


def _create_account(client, account_id: str = "chk", number: str = "1234") -> None:
    client.post(
        "/accounts",
        data={
            "account_id": account_id,
            "name": "Checking",
            "number": number,
            "description": "",
            "starting_balance": "0",
        },
    )


def _upload(
    client,
    *,
    account_id: str = "chk",
    bank: str = "Test Bank",
    content: str = SAMPLE_CSV,
):
    return client.post(
        "/import/upload",
        data={"account_id": account_id, "bank": bank},
        files={"file": ("statement.csv", content, "text/csv")},
    )


def _save_mapping(client, *, bank: str, account_id: str, file_content_b64: str):
    return client.post(
        "/import/mapping-setup/save",
        data={
            "bank": bank,
            "account_id": account_id,
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
        },
    )


def _extract_hidden_value(page_html: str, name: str) -> str:
    """Extract a hidden input's value, HTML-unescaped as a browser would."""
    marker = f'name="{name}" value="'
    start = page_html.index(marker) + len(marker)
    end = page_html.index('"', start)
    return html.unescape(page_html[start:end])


def test_import_page_renders_empty_state(client):
    response = client.get("/import")

    assert response.status_code == 200
    assert "Import transactions" in response.text


def test_upload_for_unknown_bank_shows_mapping_setup(client):
    _create_account(client)

    response = _upload(client)

    assert response.status_code == 200
    assert "First import from" in response.text
    assert "Test Bank" in response.text
    assert "description" in response.text  # header preview shows the CSV's own headers


def test_upload_rejects_blank_bank_name(client):
    _create_account(client)

    response = _upload(client, bank="   ")

    assert response.status_code == 200
    assert "Bank name is required" in response.text


def test_full_flow_saves_mapping_and_imports_filtered_rows(client):
    _create_account(client, account_id="chk", number="1234")

    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    preview_response = _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )

    assert preview_response.status_code == 200
    assert "2 new transaction" in preview_response.text
    assert "1 row filtered out" in preview_response.text
    assert "Coffee Shop" in preview_response.text
    assert "Other Account Row" not in preview_response.text

    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is not None

    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    confirm_response = client.post(
        "/import/confirm", data={"account_id": "chk", "rows_payload": rows_payload}
    )

    assert confirm_response.status_code == 200
    assert "Imported 2 transactions" in confirm_response.text
    ledger = read_ledger(config.LEDGER_PATH)
    assert len(ledger) == 2
    assert {txn.description for txn in ledger} == {"Coffee Shop", "Paycheck"}
    categories = read_categories(config.CATEGORIES_PATH)
    assert "Uncategorized" in categories["expense"]
    assert "Uncategorized" in categories["income"]


def test_second_import_from_same_bank_skips_mapping_setup(client):
    _create_account(client, account_id="chk", number="1234")
    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")
    _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )

    response = _upload(client)

    assert response.status_code == 200
    assert "First import from" not in response.text
    assert "2 new transaction" in response.text


def test_reimporting_the_same_file_is_flagged_as_duplicates(client):
    _create_account(client, account_id="chk", number="1234")
    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")
    preview_response = _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    client.post(
        "/import/confirm", data={"account_id": "chk", "rows_payload": rows_payload}
    )

    second_preview = _upload(client)

    assert "0 new transaction" in second_preview.text
    assert "2 duplicates skipped" in second_preview.text
    assert len(read_ledger(config.LEDGER_PATH)) == 2


def test_import_applies_matching_rule(client):
    from app.models.rule import Rule
    from app.storage.rules import write_rules

    _create_account(client, account_id="chk", number="1234")
    write_rules(
        [Rule(pattern="Coffee", category="Dining", subcategory="Cafes")],
        config.RULES_PATH,
    )

    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")
    preview_response = _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )

    assert "Dining / Cafes" in preview_response.text


def test_save_mapping_with_wrong_date_format_shows_error_not_a_crash(client):
    """Regression test: a bad setting must never 500 or persist a broken mapping."""
    _create_account(client, account_id="chk", number="1234")
    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    response = client.post(
        "/import/mapping-setup/save",
        data={
            "bank": "Test Bank",
            "account_id": "chk",
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%d.%m.%Y",  # wrong: the sample CSV uses %Y-%m-%d
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
        },
    )

    assert response.status_code == 200
    assert "invalid date" in response.text
    # every prior choice is preserved so only the wrong field needs fixing
    assert 'value="%d.%m.%Y"' in response.text
    assert 'value="0" selected' in response.text  # date_column
    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is None


def test_fixing_the_error_after_a_failed_save_still_works(client):
    _create_account(client, account_id="chk", number="1234")
    setup_response = _upload(client)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")
    client.post(
        "/import/mapping-setup/save",
        data={
            "bank": "Test Bank",
            "account_id": "chk",
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%d.%m.%Y",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
        },
    )

    response = _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )

    assert "2 new transaction" in response.text
    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is not None
