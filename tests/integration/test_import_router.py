"""Integration tests for the bank CSV import dashboard and its three flows:
create (``/import/new``), edit (``/import/mappings/{bank}/edit``), and run
(``/import/mappings/{bank}/run``).
"""

import html
import json

from app import config
from app.models.transaction import TransactionType
from app.services.transactions import ensure_category
from app.storage.categories import read_categories, write_categories
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


def _upload_new(
    client,
    *,
    account_id: str = "chk",
    bank: str = "Test Bank",
    content: str = SAMPLE_CSV,
):
    """Start the create flow: upload a file + account + a (hopefully new) bank name."""
    return client.post(
        "/import/new",
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


def _create_mapping(client, *, account_id: str = "chk", bank: str = "Test Bank"):
    """Full create flow: upload, mapping setup, save. Returns the preview response."""
    setup_response = _upload_new(client, account_id=account_id, bank=bank)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")
    return _save_mapping(
        client, bank=bank, account_id=account_id, file_content_b64=file_content_b64
    )


def _extract_hidden_value(page_html: str, name: str) -> str:
    """Extract a hidden input's value, HTML-unescaped as a browser would."""
    marker = f'name="{name}" value="'
    start = page_html.index(marker) + len(marker)
    end = page_html.index('"', start)
    return html.unescape(page_html[start:end])


def _confirm(client, *, account_id: str, bank: str, rows_payload: str, **extra):
    data = {
        "account_id": account_id,
        "bank": bank,
        "rows_payload": rows_payload,
        **extra,
    }
    return client.post("/import/confirm", data=data)


def test_import_page_renders_dashboard(client):
    response = client.get("/import")

    assert response.status_code == 200
    assert "Create new mapping" in response.text
    assert "No saved mappings yet" in response.text


def test_new_mapping_page_renders_upload_form(client):
    response = client.get("/import/new")

    assert response.status_code == 200
    assert 'name="bank"' in response.text
    assert 'name="file"' in response.text


def test_upload_for_new_bank_shows_mapping_setup(client):
    _create_account(client)

    response = _upload_new(client)

    assert response.status_code == 200
    assert "Test Bank" in response.text
    assert "description" in response.text  # header preview shows the CSV's own headers


def test_creating_a_mapping_with_a_duplicate_name_is_rejected(client):
    """The create flow only ever makes a *new* mapping — reuse is via Run instead."""
    _create_account(client)
    _create_mapping(client, bank="Test Bank")

    response = _upload_new(client, bank="Test Bank")

    assert response.status_code == 200
    assert "already exists" in response.text
    # rejected before reaching mapping setup at all
    assert "date_column" not in response.text


def test_mapping_setup_excludes_columns_blank_in_every_row(client):
    """Columns blank on every row (common in wide real bank exports) aren't offered."""
    _create_account(client)
    csv_with_empty_column = (
        "date,description,unused,amount\n"
        "2026-09-01,Coffee Shop,,-3.50\n"
        "2026-09-02,Paycheck,,3200.00\n"
    )

    response = _upload_new(client, content=csv_with_empty_column)

    assert response.status_code == 200
    assert ">unused<" not in response.text
    assert ">description<" in response.text


def test_upload_rejects_blank_bank_name(client):
    _create_account(client)

    response = _upload_new(client, bank="   ")

    assert response.status_code == 200
    assert "Bank name is required" in response.text


def test_full_flow_saves_mapping_and_imports_filtered_rows(client):
    _create_account(client, account_id="chk", number="1234")

    preview_response = _create_mapping(client)

    assert preview_response.status_code == 200
    assert "2 new transaction" in preview_response.text
    assert "1 row filtered out" in preview_response.text
    assert "Coffee Shop" in preview_response.text
    assert "Other Account Row" not in preview_response.text

    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is not None

    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    confirm_response = _confirm(
        client, account_id="chk", bank="Test Bank", rows_payload=rows_payload
    )

    assert confirm_response.status_code == 200
    assert "Imported 2 transactions" in confirm_response.text
    ledger = read_ledger(config.LEDGER_PATH)
    assert len(ledger) == 2
    assert {txn.description for txn in ledger} == {"Coffee Shop", "Paycheck"}
    categories = read_categories(config.CATEGORIES_PATH)
    assert "Uncategorized" in categories["expense"]
    assert "Uncategorized" in categories["income"]


def test_import_applies_matching_rule(client):
    from app.models.rule import Rule
    from app.storage.rules import write_rules

    _create_account(client, account_id="chk", number="1234")
    write_rules(
        [Rule(pattern="Coffee", category="Dining", subcategory="Cafes")],
        config.RULES_PATH,
    )

    preview_response = _create_mapping(client)

    assert "Cafes (Dining)" in preview_response.text


def test_save_mapping_with_wrong_date_format_shows_error_not_a_crash(client):
    """Regression test: a bad setting must never 500 or persist a broken mapping."""
    _create_account(client, account_id="chk", number="1234")
    setup_response = _upload_new(client)
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
    setup_response = _upload_new(client)
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


def test_reparse_shows_a_live_hint_for_a_column_blank_in_some_rows(client):
    _create_account(client, account_id="chk", number="1234")
    csv_with_gap = (
        "date,description,amount,account\n"
        "2026-09-01,Coffee Shop,-3.50,1234\n"
        "2026-09-02,Paycheck,,1234\n"
    )
    setup_response = _upload_new(client, content=csv_with_gap)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    response = client.post(
        "/import/mapping-setup/reparse",
        data={
            "bank": "Test Bank",
            "account_id": "chk",
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "amount_column": "2",
        },
    )

    assert response.status_code == 200
    assert "blank in 1 of 2 rows" in response.text


def test_preview_warns_about_rows_skipped_for_missing_amount(client):
    _create_account(client, account_id="chk", number="1234")
    csv_with_gap = (
        "date,description,amount,account\n"
        "2026-09-01,Coffee Shop,-3.50,1234\n"
        "2026-09-02,Paycheck,,1234\n"
    )
    setup_response = _upload_new(client, content=csv_with_gap)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    response = _save_mapping(
        client, bank="Test Bank", account_id="chk", file_content_b64=file_content_b64
    )

    assert response.status_code == 200
    assert "1 new transaction" in response.text
    assert "1 of 2 rows had no amount" in response.text


def test_description_fallback_column_used_when_primary_is_blank(client):
    _create_account(client, account_id="chk", number="1234")
    csv_with_payee = (
        "date,description,amount,account,payee\n"
        "2026-09-01,,-3.50,1234,Tauron Sprzedaz\n"
    )
    setup_response = _upload_new(client, content=csv_with_payee)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    response = client.post(
        "/import/mapping-setup/save",
        data={
            "bank": "Test Bank",
            "account_id": "chk",
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "description_fallback_column": "4",
            "amount_column": "2",
            "account_number_column": "3",
        },
    )

    assert response.status_code == 200
    assert "Tauron Sprzedaz" in response.text


def test_dashboard_lists_saved_mapping_and_history(client):
    _create_account(client, account_id="chk", number="1234")
    preview_response = _create_mapping(client)
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    _confirm(client, account_id="chk", bank="Test Bank", rows_payload=rows_payload)

    response = client.get("/import")

    assert response.status_code == 200
    assert "Test Bank" in response.text
    assert "Checking" in response.text
    assert "2" in response.text  # new_count shown in the history row


def test_delete_mapping_removes_it_but_keeps_history(client):
    _create_account(client, account_id="chk", number="1234")
    preview_response = _create_mapping(client)
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    _confirm(client, account_id="chk", bank="Test Bank", rows_payload=rows_payload)
    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is not None

    response = client.post("/import/mappings/Test%20Bank/delete")

    assert response.status_code == 200
    assert read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR) is None
    assert "Test Bank" in response.text  # still present in history, just not mappings


# ---------------------------------------------------------------------------
# Edit flow: no file needed, Save persists unconditionally, Dry run is read-only
# ---------------------------------------------------------------------------


def test_edit_page_shows_saved_settings_without_asking_for_file(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.get("/import/mappings/Test%20Bank/edit")

    assert response.status_code == 200
    assert "Test Bank" in response.text
    assert 'value="0"' in response.text  # saved date_column
    assert "First import from" not in response.text  # no file-upload step 1 text


def test_edit_for_unknown_bank_404s(client):
    response = client.get("/import/mappings/Nope/edit")

    assert response.status_code == 404


def test_saving_edited_mapping_persists_unconditionally(client):
    """Save just persists whatever's typed — no file, no parse-check required."""
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.post(
        "/import/mappings/Test%20Bank/edit",
        data={
            "delimiter": ";",
            "encoding": "utf-8",
            "date_format": "%d/%m/%Y",
            "decimal_separator": ",",
            "date_column": "5",
            "description_column": "6",
            "amount_column": "7",
        },
    )

    assert response.status_code == 200
    mapping = read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR)
    assert mapping.delimiter == ";"
    assert mapping.date_format == "%d/%m/%Y"
    assert mapping.columns == {"date": 5, "description": 6, "amount": 7}


def test_saving_edited_mapping_persists_the_extra_description_columns(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    client.post(
        "/import/mappings/Test%20Bank/edit",
        data={
            "delimiter": ";",
            "encoding": "utf-8",
            "date_format": "%d/%m/%Y",
            "decimal_separator": ",",
            "date_column": "5",
            "description_column": "6",
            "amount_column": "7",
            "description_extra_1_column": "8",
            "description_extra_2_column": "",
        },
    )

    mapping = read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR)
    assert mapping.columns["description_extra_1"] == 8
    assert "description_extra_2" not in mapping.columns  # blank means none


def test_edit_form_shows_the_extra_description_columns(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    text = client.get("/import/mappings/Test%20Bank/edit").text

    assert 'name="description_extra_1_column"' in text
    assert 'name="description_extra_2_column"' in text


def test_dry_run_edited_mapping_is_read_only(client):
    """Dry run against edited-but-unsaved settings never writes to the ledger."""
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.post(
        "/import/mappings/Test%20Bank/dry-run",
        data={
            "account_id": "chk",
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
        },
        files={"file": ("statement.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    assert "Dry run" in response.text
    assert "Coffee Shop" in response.text
    assert "Confirm import" not in response.text
    # the settings sent to dry-run were never saved
    saved = read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR)
    assert saved.columns["account_number"] == 3
    assert len(read_ledger(config.LEDGER_PATH)) == 0


def test_dry_run_without_a_file_reprompts_with_an_error(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.post(
        "/import/mappings/Test%20Bank/dry-run",
        data={
            "account_id": "chk",
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
        },
    )

    assert response.status_code == 200
    assert "Choose a CSV file" in response.text


# ---------------------------------------------------------------------------
# Run flow: reuse a saved mapping against a newly uploaded file
# ---------------------------------------------------------------------------


def test_run_page_prompts_for_account_and_file(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.get("/import/mappings/Test%20Bank/run")

    assert response.status_code == 200
    assert 'name="account_id"' in response.text
    assert 'name="file"' in response.text


def test_run_for_unknown_bank_404s(client):
    response = client.get("/import/mappings/Nope/run")

    assert response.status_code == 404


def test_running_a_saved_mapping_imports_against_it(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    response = client.post(
        "/import/mappings/Test%20Bank/run",
        data={"account_id": "chk"},
        files={"file": ("statement.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    assert "2 new transaction" in response.text
    rows_payload = _extract_hidden_value(response.text, "rows_payload")
    confirm_response = _confirm(
        client, account_id="chk", bank="Test Bank", rows_payload=rows_payload
    )

    assert "Imported 2 transactions" in confirm_response.text
    assert len(read_ledger(config.LEDGER_PATH)) == 2


def test_reimporting_the_same_file_via_run_is_flagged_as_duplicates(client):
    _create_account(client, account_id="chk", number="1234")
    preview_response = _create_mapping(client)
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")
    _confirm(client, account_id="chk", bank="Test Bank", rows_payload=rows_payload)

    second_preview = client.post(
        "/import/mappings/Test%20Bank/run",
        data={"account_id": "chk"},
        files={"file": ("statement.csv", SAMPLE_CSV, "text/csv")},
    )

    assert "0 new transaction" in second_preview.text
    assert "2 duplicates skipped" in second_preview.text
    assert len(read_ledger(config.LEDGER_PATH)) == 2


def test_confirmed_import_captures_counterparty_account(client):
    _create_account(client, account_id="chk", number="1234")
    csv_text = (
        "date,description,amount,account,counterparty\n"
        "2026-09-01,To savings,-50.00,1234,5678\n"
    )
    setup_response = _upload_new(client, bank="CP Bank", content=csv_text)
    file_content_b64 = _extract_hidden_value(setup_response.text, "file_content_b64")

    save_response = client.post(
        "/import/mapping-setup/save",
        data={
            "bank": "CP Bank",
            "account_id": "chk",
            "file_content_b64": file_content_b64,
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
            "counterparty_account_column": "4",
        },
    )
    rows_payload = _extract_hidden_value(save_response.text, "rows_payload")
    _confirm(client, account_id="chk", bank="CP Bank", rows_payload=rows_payload)

    [txn] = read_ledger(config.LEDGER_PATH)
    assert txn.counterparty_account == "5678"


def test_preview_offers_a_category_picker_per_row(client):
    _create_account(client, account_id="chk", number="1234")
    write_categories(
        ensure_category(read_categories(), TransactionType.EXPENSE, "Food", "Coffee")
    )

    preview_response = _create_mapping(client)

    assert preview_response.text.count('name="row_categories"') == 2
    assert preview_response.text.count('name="row_notes"') == 2
    assert 'id="cat-options-expense"' in preview_response.text
    assert 'form="import-confirm-form"' in preview_response.text
    assert 'id="import-confirm-form"' in preview_response.text


def test_confirm_applies_picked_categories_but_never_creates_new_ones(client):
    _create_account(client, account_id="chk", number="1234")
    write_categories(
        ensure_category(read_categories(), TransactionType.EXPENSE, "Food", "Coffee")
    )
    preview_response = _create_mapping(client)
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")

    # Row 1 (Coffee Shop, expense): a real pair. Row 2 (Paycheck, income):
    # "Food" is an expense category, so the pick is ignored.
    confirm_response = _confirm(
        client,
        account_id="chk",
        bank="Test Bank",
        rows_payload=rows_payload,
        row_categories=[json.dumps(["Food", "Coffee"]), json.dumps(["Food", ""])],
    )

    assert confirm_response.status_code == 200
    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert (
        by_description["Coffee Shop"].category,
        by_description["Coffee Shop"].subcategory,
    ) == (
        "Food",
        "Coffee",
    )
    assert by_description["Paycheck"].category == "Uncategorized"


_EXTRAS_CSV = (
    "date,description,amount,account,site,detail\n"
    "2026-09-01,PayU,-9.00,1234,cinema.pl,Godzilla\n"
    "2026-09-02,Zabka,-3.00,1234,,\n"
)


def _save_extras_mapping(client, *, with_extras: bool):
    setup = _upload_new(client, bank="X Bank", content=_EXTRAS_CSV)
    data = {
        "bank": "X Bank",
        "account_id": "chk",
        "file_content_b64": _extract_hidden_value(setup.text, "file_content_b64"),
        "delimiter": ",",
        "encoding": "utf-8",
        "date_format": "%Y-%m-%d",
        "decimal_separator": ".",
        "date_column": "0",
        "description_column": "1",
        "amount_column": "2",
        "account_number_column": "3",
    }
    if with_extras:
        data["description_extra_1_column"] = "4"
        data["description_extra_2_column"] = "5"
    return client.post("/import/mapping-setup/save", data=data)


def test_extra_description_columns_prefill_the_preview_and_save_as_the_note(client):
    _create_account(client, account_id="chk", number="1234")

    preview = _save_extras_mapping(client, with_extras=True)

    assert 'value="PayU | cinema.pl | Godzilla"' in preview.text
    assert 'value="Zabka"' in preview.text  # nothing extra to show
    rows_payload = _extract_hidden_value(preview.text, "rows_payload")
    _confirm(
        client,
        account_id="chk",
        bank="X Bank",
        rows_payload=rows_payload,
        row_notes=["PayU | cinema.pl | Godzilla", "Zabka"],  # what the form submits
    )
    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert by_description["PayU"].notes == "PayU | cinema.pl | Godzilla"
    assert by_description["Zabka"].notes is None


def test_adding_extras_later_does_not_reimport_what_is_already_there(client):
    _create_account(client, account_id="chk", number="1234")
    first = _save_extras_mapping(client, with_extras=False)
    _confirm(
        client,
        account_id="chk",
        bank="X Bank",
        rows_payload=_extract_hidden_value(first.text, "rows_payload"),
    )
    assert len(read_ledger(config.LEDGER_PATH)) == 2

    client.post(  # the user edits the saved mapping to add the extra columns
        "/import/mappings/X%20Bank/edit",
        data={
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
            "description_extra_1_column": "4",
            "description_extra_2_column": "5",
        },
    )

    second = client.post(
        "/import/mappings/X%20Bank/run",
        data={"account_id": "chk"},
        files={"file": ("statement.csv", _EXTRAS_CSV, "text/csv")},
    )

    assert "0 new transaction" in second.text
    assert "2 duplicates skipped" in second.text


_PAYEE_CSV = (
    "date,description,amount,account,merchant,recipient\n"
    "2026-09-01,PayU,-9.00,1234,Bank Cinema,\n"
    "2026-09-02,Wire 5,-30.00,1234,,Tauron SA\n"
    "2026-09-03,Cash,-4.00,1234,,\n"
)


def _save_payee_mapping(client, *, content=_PAYEE_CSV):
    setup = _upload_new(client, bank="P Bank", content=content)
    return client.post(
        "/import/mapping-setup/save",
        data={
            "bank": "P Bank",
            "account_id": "chk",
            "file_content_b64": _extract_hidden_value(setup.text, "file_content_b64"),
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
            "payee_column": "4",
            "payee_fallback_column": "5",
        },
    )


def test_payee_columns_prefill_the_preview_and_are_saved_on_confirm(client):
    _create_account(client, account_id="chk", number="1234")

    preview = _save_payee_mapping(client)

    assert 'value="Bank Cinema"' in preview.text
    assert 'value="Tauron SA"' in preview.text  # fallback column
    assert 'name="row_payees"' in preview.text
    _confirm(
        client,
        account_id="chk",
        bank="P Bank",
        rows_payload=_extract_hidden_value(preview.text, "rows_payload"),
        row_payees=["Bank Cinema", "Tauron SA", ""],
    )
    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert by_description["PayU"].payee == "Bank Cinema"
    assert by_description["Wire 5"].payee == "Tauron SA"
    assert by_description["Cash"].payee == ""


def test_preview_payee_can_be_edited_before_confirming(client):
    _create_account(client, account_id="chk", number="1234")
    preview = _save_payee_mapping(client)

    _confirm(
        client,
        account_id="chk",
        bank="P Bank",
        rows_payload=_extract_hidden_value(preview.text, "rows_payload"),
        row_payees=["Cinema City", "", "Kiosk"],  # renamed, cleared, newly named
    )

    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert by_description["PayU"].payee == "Cinema City"
    assert by_description["Wire 5"].payee == ""
    assert by_description["Cash"].payee == "Kiosk"


def test_a_rule_payee_beats_the_banks_payee_in_the_preview(client):
    from app.models.rule import Rule
    from app.storage.rules import write_rules

    _create_account(client, account_id="chk", number="1234")
    write_rules(
        [Rule(pattern="PayU", category="Fun", payee="Cinema City")], config.RULES_PATH
    )

    preview = _save_payee_mapping(client)

    assert 'value="Cinema City"' in preview.text
    assert 'value="Bank Cinema"' not in preview.text


def test_edit_form_round_trips_the_payee_columns(client):
    _create_account(client, account_id="chk", number="1234")
    _create_mapping(client)

    client.post(
        "/import/mappings/Test%20Bank/edit",
        data={
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "payee_column": "4",
            "payee_fallback_column": "",
        },
    )

    mapping = read_mapping("Test Bank", config.IMPORT_MAPPINGS_DIR)
    assert mapping.columns["payee"] == 4
    assert "payee_fallback" not in mapping.columns
    form = client.get("/import/mappings/Test%20Bank/edit").text
    assert 'name="payee_column"' in form and 'value="4"' in form


def test_confirm_stores_an_edited_description_as_a_note_only(client):
    _create_account(client, account_id="chk", number="1234")
    preview_response = _create_mapping(client)
    rows_payload = _extract_hidden_value(preview_response.text, "rows_payload")

    _confirm(
        client,
        account_id="chk",
        bank="Test Bank",
        rows_payload=rows_payload,
        row_notes=["Morning latte", "Paycheck"],
    )

    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert by_description["Coffee Shop"].notes == "Morning latte"
    assert by_description["Paycheck"].notes is None


def test_confirmed_import_marks_every_row_with_the_mapping_it_came_from(client):
    _create_account(client, account_id="chk", number="1234")
    preview = _create_mapping(client)  # bank "Test Bank"

    _confirm(
        client,
        account_id="chk",
        bank="Test Bank",
        rows_payload=_extract_hidden_value(preview.text, "rows_payload"),
    )

    ledger = read_ledger(config.LEDGER_PATH)
    assert len(ledger) == 2
    assert {t.source for t in ledger} == {"Test Bank"}


def test_manual_entries_have_no_source(client):
    _create_account(client, account_id="chk", number="1234")
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": "2026-01-15",
            "type": "expense",
            "category": "Food",
            "subcategory": "",
            "description": "cash",
            "amount": "5.00",
            "notes": "",
        },
    )

    assert [t.source for t in read_ledger(config.LEDGER_PATH)] == [""]


def _save_with_fields(client, labels, columns, content=_PAYEE_CSV, bank="F Bank"):
    setup = _upload_new(client, bank=bank, content=content)
    return client.post(
        "/import/mapping-setup/save",
        data={
            "bank": bank,
            "account_id": "chk",
            "file_content_b64": _extract_hidden_value(setup.text, "file_content_b64"),
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
            "payee_field_label": labels,
            "payee_field_column": columns,
        },
    )


def test_setup_offers_five_payee_field_slots(client):
    _create_account(client, account_id="chk", number="1234")
    setup = _upload_new(client, bank="F Bank", content=_PAYEE_CSV)

    assert setup.text.count('name="payee_field_label"') == 5
    assert setup.text.count('name="payee_field_column"') == 5


def test_payee_fields_are_saved_in_priority_order_and_named_after_the_header(client):
    _create_account(client, account_id="chk", number="1234")

    _save_with_fields(client, ["", "Who", "", "", ""], ["5", "4", "", "", ""])

    mapping = read_mapping("F Bank", config.IMPORT_MAPPINGS_DIR)
    assert [(f.label, f.column) for f in mapping.payee_fields] == [
        ("recipient", 5),  # no label typed: the column's header
        ("Who", 4),
    ]


def test_import_stores_the_payee_field_values_on_each_row_by_label(client):
    _create_account(client, account_id="chk", number="1234")
    preview = _save_with_fields(client, ["Merchant", "Recipient"], ["4", "5"])

    _confirm(
        client,
        account_id="chk",
        bank="F Bank",
        rows_payload=_extract_hidden_value(preview.text, "rows_payload"),
    )

    by_description = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert by_description["PayU"].payee_fields == {"Merchant": "Bank Cinema"}
    assert by_description["Wire 5"].payee_fields == {"Recipient": "Tauron SA"}
    assert by_description["Cash"].payee_fields == {}  # both cells blank


def test_edit_form_shows_and_saves_the_payee_field_slots(client):
    _create_account(client, account_id="chk", number="1234")
    _save_with_fields(client, ["Merchant"], ["4"])

    form = client.get("/import/mappings/F%20Bank/edit").text
    assert form.count('name="payee_field_label"') == 5
    assert 'value="Merchant"' in form

    client.post(
        "/import/mappings/F%20Bank/edit",
        data={
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "payee_field_label": ["Recipient", "", "Merchant"],
            "payee_field_column": ["5", "", "4"],
        },
    )

    mapping = read_mapping("F Bank", config.IMPORT_MAPPINGS_DIR)
    assert [(f.label, f.column) for f in mapping.payee_fields] == [
        ("Recipient", 5),
        ("Merchant", 4),  # a slot without a column is dropped; order kept
    ]


def test_payee_field_labels_cannot_clash_with_built_in_fields(client):
    _create_account(client, account_id="chk", number="1234")

    _save_with_fields(client, ["Description", "description"], ["4", "5"])

    mapping = read_mapping("F Bank", config.IMPORT_MAPPINGS_DIR)
    assert [f.label for f in mapping.payee_fields] == ["Description 2", "description 3"]


def test_the_mapping_list_shows_the_payee_field_labels(client):
    _create_account(client, account_id="chk", number="1234")
    _save_with_fields(client, ["Merchant"], ["4"])

    assert "Merchant: 4" in client.get("/import").text


# ---------------------------------------------------------------------------
# Fill payee fields on existing transactions from a CSV
# ---------------------------------------------------------------------------


def _import_without_fields(client, bank="E Bank"):
    """Import _PAYEE_CSV with a mapping that captures no payee fields."""
    _create_account(client, account_id="chk", number="1234")
    preview = _save_with_fields(client, [], [], bank=bank)
    _confirm(
        client,
        account_id="chk",
        bank=bank,
        rows_payload=_extract_hidden_value(preview.text, "rows_payload"),
    )
    assert all(t.payee_fields == {} for t in read_ledger(config.LEDGER_PATH))


def _add_fields_to_mapping(client, bank="E Bank"):
    client.post(
        f"/import/mappings/{bank.replace(' ', '%20')}/edit",
        data={
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_separator": ".",
            "date_column": "0",
            "description_column": "1",
            "amount_column": "2",
            "account_number_column": "3",
            "payee_field_label": ["Merchant", "Recipient"],
            "payee_field_column": ["4", "5"],
        },
    )


def _enrich(client, bank="E Bank", content=_PAYEE_CSV):
    return client.post(
        f"/import/mappings/{bank.replace(' ', '%20')}/enrich",
        data={"account_id": "chk"},
        files={"file": ("old.csv", content, "text/csv")},
    )


def test_the_enrich_page_explains_itself_and_lists_the_fields(client):
    _import_without_fields(client)
    _add_fields_to_mapping(client)

    text = client.get("/import/mappings/E%20Bank/enrich").text

    assert "Merchant, Recipient" in text
    assert 'name="file"' in text and 'name="account_id"' in text


def test_enrich_previews_then_fills_fields_without_adding_rows(client):
    _import_without_fields(client)
    _add_fields_to_mapping(client)
    before = [t.id for t in read_ledger(config.LEDGER_PATH)]

    preview = _enrich(client)

    assert "<strong>2</strong> existing transaction" in preview.text
    assert "Bank Cinema" in preview.text and "Tauron SA" in preview.text
    assert [t.payee_fields for t in read_ledger(config.LEDGER_PATH)] == [{}, {}, {}]
    done = client.post(
        "/import/mappings/E%20Bank/enrich/apply",
        data={
            "updates_payload": _extract_hidden_value(preview.text, "updates_payload")
        },
    )

    assert "<strong>2</strong>" in done.text
    ledger = {t.description: t for t in read_ledger(config.LEDGER_PATH)}
    assert [t.id for t in read_ledger(config.LEDGER_PATH)] == before  # nothing added
    assert ledger["PayU"].payee_fields == {"Merchant": "Bank Cinema"}
    assert ledger["Wire 5"].payee_fields == {"Recipient": "Tauron SA"}
    assert ledger["Cash"].payee_fields == {}  # both cells blank in the file
    assert len(ledger) == 3


def test_enrich_never_changes_a_value_already_saved(client):
    _import_without_fields(client)
    _add_fields_to_mapping(client)
    first = _enrich(client)
    client.post(
        "/import/mappings/E%20Bank/enrich/apply",
        data={"updates_payload": _extract_hidden_value(first.text, "updates_payload")},
    )
    changed_file = _PAYEE_CSV.replace("Bank Cinema", "Different")

    again = _enrich(client, content=changed_file)

    assert "<strong>0</strong> existing transaction" in again.text
    assert "2 already have them" in " ".join(again.text.split())
    assert {t.description: t.payee_fields for t in read_ledger(config.LEDGER_PATH)}[
        "PayU"
    ] == {"Merchant": "Bank Cinema"}


def test_enrich_needs_a_mapping_with_payee_fields(client):
    _import_without_fields(client)  # mapping has none

    page = _enrich(client)

    assert "no payee fields yet" in page.text
    assert client.get("/import/mappings/Nope/enrich").status_code == 404


def test_the_mapping_list_links_to_enrich_only_when_there_are_payee_fields(client):
    _import_without_fields(client)
    assert "Fill payee fields" not in client.get("/import").text

    _add_fields_to_mapping(client)

    assert "Fill payee fields" in client.get("/import").text


def test_enrich_ignores_a_payload_row_for_a_missing_transaction(client):
    _import_without_fields(client)

    done = client.post(
        "/import/mappings/E%20Bank/enrich/apply",
        data={"updates_payload": '{"ghost": {"Merchant": "x"}}'},
    )

    assert "<strong>0</strong>" in done.text
