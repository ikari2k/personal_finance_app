"""Tests for app.services.importer."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.import_mapping import ImportMapping
from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType
from app.services.importer import (
    DEFAULT_CATEGORY,
    ParsedRow,
    build_transactions,
    filter_by_account_number,
    find_duplicates,
    parse_amount,
    parse_date,
    parse_rows,
)


def _txn(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 9, 10),
        "account_id": "chk",
        "category": "Groceries",
        "subcategory": "",
        "description": "Zabka",
        "amount": Decimal("-5.99"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_parse_amount_handles_european_comma_and_currency_suffix():
    assert parse_amount("-5,99 PLN", ",") == Decimal("-5.99")


def test_parse_amount_handles_thousands_separator():
    assert parse_amount("13 855,62 PLN", ",") == Decimal("13855.62")


def test_parse_amount_handles_us_style_thousands_comma():
    assert parse_amount("1,234.56", ".") == Decimal("1234.56")


def test_parse_amount_rejects_unparseable_value():
    with pytest.raises(ValueError):
        parse_amount("not a number", ".")


def test_parse_date_parses_dd_mm_yyyy():
    assert parse_date("11.09.2026", "%d.%m.%Y") == date(2026, 9, 11)


def test_parse_date_rejects_mismatched_format():
    with pytest.raises(ValueError):
        parse_date("2026-09-11", "%d.%m.%Y")


CA_MAPPING = ImportMapping(
    bank="Credit Agricole",
    delimiter=";",
    date_format="%d.%m.%Y",
    decimal_separator=",",
    columns={"date": 3, "description": 2, "amount": 1, "account_number": 0},
)


def test_parse_rows_extracts_fields_by_index():
    csv_text = (
        "account;amount;description;date\n"
        "45 1940 0000;-5,99 PLN;ZABKA Z8540;11.09.2026\n"
    )

    rows = parse_rows(csv_text, CA_MAPPING)

    assert rows == [
        ParsedRow(
            date=date(2026, 9, 11),
            description="ZABKA Z8540",
            amount=Decimal("-5.99"),
            account_number="45 1940 0000",
        )
    ]


def test_parse_rows_skips_blank_and_zero_amount_rows():
    csv_text = (
        "account;amount;description;date\n"
        "45 1940 0000;0,00 PLN;no-op;11.09.2026\n"
        ";;;\n"
        "45 1940 0000;-1,00 PLN;real row;10.09.2026\n"
    )

    rows = parse_rows(csv_text, CA_MAPPING)

    assert len(rows) == 1
    assert rows[0].description == "real row"


def test_parse_rows_skips_rows_with_a_blank_date():
    csv_text = (
        "account;amount;description;date\n"
        "45 1940 0000;-10,00 PLN;account fee, no operation date;\n"
        "45 1940 0000;-1,00 PLN;real row;10.09.2026\n"
    )

    rows = parse_rows(csv_text, CA_MAPPING)

    assert len(rows) == 1
    assert rows[0].description == "real row"


def test_parse_rows_still_raises_when_a_non_blank_date_is_unparseable():
    csv_text = (
        "account;amount;description;date\n45 1940 0000;-1,00 PLN;row;not-a-date\n"
    )

    with pytest.raises(ValueError):
        parse_rows(csv_text, CA_MAPPING)


def test_parse_rows_without_account_number_column_leaves_it_blank():
    mapping = ImportMapping(
        bank="Generic",
        delimiter=",",
        date_format="%Y-%m-%d",
        columns={"date": 0, "description": 1, "amount": 2},
    )
    csv_text = "date,description,amount\n2026-09-11,Coffee,-3.50\n"

    rows = parse_rows(csv_text, mapping)

    assert rows[0].account_number == ""


def test_filter_by_account_number_keeps_matching_rows():
    rows = [
        ParsedRow(date(2026, 9, 1), "a", Decimal("-1"), "45 1940 0000"),
        ParsedRow(date(2026, 9, 2), "b", Decimal("-2"), "85 1940 9999"),
    ]

    kept, skipped = filter_by_account_number(rows, "45 1940 0000")

    assert kept == [rows[0]]
    assert skipped == 1


def test_filter_by_account_number_ignores_whitespace_and_case():
    rows = [ParsedRow(date(2026, 9, 1), "a", Decimal("-1"), "45 1940 0000")]

    kept, skipped = filter_by_account_number(rows, "451940 0000")

    assert kept == rows
    assert skipped == 0


def test_filter_by_account_number_keeps_rows_with_no_account_info():
    rows = [ParsedRow(date(2026, 9, 1), "a", Decimal("-1"), "")]

    kept, skipped = filter_by_account_number(rows, "45 1940 0000")

    assert kept == rows
    assert skipped == 0


def test_filter_by_account_number_no_target_keeps_everything():
    rows = [ParsedRow(date(2026, 9, 1), "a", Decimal("-1"), "45 1940 0000")]

    kept, skipped = filter_by_account_number(rows, "")

    assert kept == rows
    assert skipped == 0


def test_find_duplicates_matches_date_amount_description_same_account():
    existing = [_txn(account_id="chk", date=date(2026, 9, 10), amount=Decimal("-5.99"))]
    rows = [ParsedRow(date(2026, 9, 10), "Zabka", Decimal("-5.99"), "")]

    new_rows, duplicates = find_duplicates(rows, existing, "chk")

    assert new_rows == []
    assert duplicates == rows


def test_find_duplicates_is_scoped_to_the_target_account():
    existing = [_txn(account_id="sav", date=date(2026, 9, 10), amount=Decimal("-5.99"))]
    rows = [ParsedRow(date(2026, 9, 10), "Zabka", Decimal("-5.99"), "")]

    new_rows, duplicates = find_duplicates(rows, existing, "chk")

    assert new_rows == rows
    assert duplicates == []


def test_find_duplicates_description_match_is_case_and_whitespace_insensitive():
    existing = [
        _txn(
            account_id="chk",
            date=date(2026, 9, 10),
            amount=Decimal("-5.99"),
            description="  zabka  ",
        )
    ]
    rows = [ParsedRow(date(2026, 9, 10), "ZABKA", Decimal("-5.99"), "")]

    new_rows, duplicates = find_duplicates(rows, existing, "chk")

    assert new_rows == []
    assert duplicates == rows


def test_build_transactions_derives_type_from_amount_sign():
    rows = [
        ParsedRow(date(2026, 9, 1), "Zabka", Decimal("-5.99"), ""),
        ParsedRow(date(2026, 9, 2), "Paycheck", Decimal("3200.00"), ""),
    ]

    transactions = build_transactions(rows, "chk", [])

    assert transactions[0].type is TransactionType.EXPENSE
    assert transactions[1].type is TransactionType.INCOME


def test_build_transactions_applies_matching_rule():
    rows = [ParsedRow(date(2026, 9, 1), "ZABKA Z8540", Decimal("-5.99"), "")]
    rules = [Rule(pattern="ZABKA", category="Groceries", subcategory="Supermarket")]

    transactions = build_transactions(rows, "chk", rules)

    assert transactions[0].category == "Groceries"
    assert transactions[0].subcategory == "Supermarket"


def test_build_transactions_defaults_to_uncategorized_when_no_rule_matches():
    rows = [ParsedRow(date(2026, 9, 1), "Unknown Merchant", Decimal("-5.99"), "")]

    transactions = build_transactions(rows, "chk", [])

    assert transactions[0].category == DEFAULT_CATEGORY
    assert transactions[0].subcategory == ""


def test_build_transactions_never_produces_a_transfer():
    rows = [ParsedRow(date(2026, 9, 1), "x", Decimal("-1"), "")]

    transactions = build_transactions(rows, "chk", [])

    assert transactions[0].type is not TransactionType.TRANSFER
    assert transactions[0].transfer_id is None
