"""Tests for app.services.importer."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.import_mapping import ImportMapping, PayeeFieldColumn
from app.models.payee import DESCRIPTION_FIELD, PayeeRule
from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType
from app.services.importer import (
    DEFAULT_CATEGORY,
    ParsedRow,
    apply_enrichment,
    apply_payee_edits,
    build_transactions,
    count_blank_column_values,
    filter_by_account_number,
    find_duplicates,
    merge_payee_fields,
    parse_amount,
    parse_date,
    parse_rows,
    plan_enrichment,
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


def test_parse_rows_uses_fallback_when_description_is_blank():
    mapping = ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "description_fallback": 4,
        },
    )
    csv_text = (
        "account;amount;description;date;payee\n"
        "45 1940 0000;-1,00 PLN;;11.09.2026;Tauron Sprzedaz\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].description == "Tauron Sprzedaz"


def test_parse_rows_prefers_primary_description_over_fallback():
    mapping = ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "description_fallback": 4,
        },
    )
    csv_text = (
        "account;amount;description;date;payee\n"
        "45 1940 0000;-1,00 PLN;ZABKA Z8540;11.09.2026;Should not be used\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].description == "ZABKA Z8540"


def _extras_mapping(**columns) -> ImportMapping:
    return ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "description_fallback": 4,
            "description_extra_1": 5,
            "description_extra_2": 6,
            **columns,
        },
    )


_EXTRAS_HEADER = "account;amount;description;date;payee;site;detail\n"


def test_parse_rows_collects_extra_columns_for_display_without_changing_description():
    csv_text = _EXTRAS_HEADER + "1;-1,00 PLN;PayU;11.09.2026;x;cinema.pl;Godzilla\n"

    rows = parse_rows(csv_text, _extras_mapping())

    assert rows[0].description == "PayU"  # still the bank's own text
    assert rows[0].display_description == "PayU | cinema.pl | Godzilla"


def test_parse_rows_skips_blank_and_repeated_extras():
    csv_text = (
        _EXTRAS_HEADER
        + "1;-1,00 PLN;PayU;11.09.2026;x;;Godzilla\n"
        + "2;-2,00 PLN;PayU;12.09.2026;x;PayU;\n"
    )

    rows = parse_rows(csv_text, _extras_mapping())

    assert rows[0].display_description == "PayU | Godzilla"
    assert rows[1].display_description == ""  # nothing new to add
    assert rows[1].description == "PayU"


def test_parse_rows_extras_follow_the_fallback_when_the_primary_is_blank():
    csv_text = _EXTRAS_HEADER + "1;-1,00 PLN;;11.09.2026;Tauron;;Faktura 5\n"

    rows = parse_rows(csv_text, _extras_mapping())

    assert rows[0].description == "Tauron"
    assert rows[0].display_description == "Tauron | Faktura 5"


def test_parse_rows_without_extra_columns_has_no_display_description():
    mapping = _extras_mapping()
    for key in ("description_extra_1", "description_extra_2"):
        del mapping.columns[key]
    csv_text = _EXTRAS_HEADER + "1;-1,00 PLN;PayU;11.09.2026;x;cinema.pl;Godzilla\n"

    rows = parse_rows(csv_text, mapping)

    assert rows[0].description == "PayU"
    assert rows[0].display_description == ""


def test_build_transactions_carries_the_display_text_as_the_preview_note():
    rows = [
        ParsedRow(
            date(2026, 9, 10),
            "PayU",
            Decimal("-5.00"),
            display_description="PayU | cinema.pl",
        ),
        ParsedRow(date(2026, 9, 11), "Zabka", Decimal("-3.00")),
    ]

    with_extras, plain = build_transactions(rows, "chk", [])

    assert with_extras.description == "PayU"
    assert with_extras.notes == "PayU | cinema.pl"
    assert plain.notes is None


def test_find_duplicates_ignores_the_extra_description_text():
    row = ParsedRow(
        date(2026, 9, 10),
        "Zabka",
        Decimal("-5.99"),
        display_description="Zabka | zabka.pl",
    )
    stored = _txn(description="Zabka", notes="Zabka | zabka.pl")

    assert find_duplicates([row], [stored], "chk") == ([], [row])
    assert find_duplicates([row], [], "chk") == ([row], [])


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


def test_parse_rows_skips_rows_with_a_blank_amount():
    csv_text = (
        "account;amount;description;date\n"
        "45 1940 0000;;domestic, no fx amount;11.09.2026\n"
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


def test_count_blank_column_values_counts_blanks_in_one_column():
    csv_text = (
        "account;amount;description;date\n"
        "45 1940 0000;-1,00 PLN;a;11.09.2026\n"
        "45 1940 0000;-2,00 PLN;b;\n"
        "45 1940 0000;-3,00 PLN;c;\n"
    )

    blank, total = count_blank_column_values(csv_text, ";", 3)

    assert (blank, total) == (2, 3)


def test_count_blank_column_values_ignores_fully_blank_rows():
    csv_text = "account;amount;description;date\n;;;\n45 1940 0000;-1,00 PLN;a;\n"

    blank, total = count_blank_column_values(csv_text, ";", 3)

    assert (blank, total) == (1, 1)


def test_count_blank_column_values_no_data_rows():
    assert count_blank_column_values("account;amount", ";", 0) == (0, 0)


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


def test_parse_rows_extracts_and_normalizes_counterparty_account():
    mapping = ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "counterparty_account": 4,
        },
    )
    csv_text = (
        "account;amount;description;date;counterparty\n"
        "45 1940 0000;-1,00 PLN;ZABKA;11.09.2026;85 1940 1018 2000\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].counterparty_account == "85194010182000"


def test_parse_rows_uses_counterparty_fallback_when_primary_is_blank():
    mapping = ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "counterparty_account": 4,
            "counterparty_account_fallback": 5,
        },
    )
    csv_text = (
        "account;amount;description;date;recipient;sender\n"
        "45 1940 0000;1,00 PLN;ZABKA;11.09.2026;;85 1940 1018 2000\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].counterparty_account == "85194010182000"


def test_parse_rows_prefers_primary_counterparty_over_fallback():
    mapping = ImportMapping(
        bank="Credit Agricole",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={
            "date": 3,
            "description": 2,
            "amount": 1,
            "counterparty_account": 4,
            "counterparty_account_fallback": 5,
        },
    )
    csv_text = (
        "account;amount;description;date;recipient;sender\n"
        "45 1940 0000;1,00 PLN;ZABKA;11.09.2026;11 22;Should not be used\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].counterparty_account == "1122"


def test_parse_rows_without_counterparty_columns_leaves_it_blank():
    rows = parse_rows(
        "account;amount;description;date\n45 1940 0000;-1,00 PLN;ZABKA;11.09.2026\n",
        CA_MAPPING,
    )

    assert rows[0].counterparty_account == ""


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


def test_build_transactions_takes_the_payee_from_a_matching_rule():
    rules = [Rule(pattern="PayU", category="Fun", payee="Cinema City")]
    rows = [ParsedRow(date(2026, 9, 10), "PayU", Decimal("-9"), payee="Bank name")]

    [txn] = build_transactions(rows, "chk", rules)

    assert txn.payee == "Cinema City"  # the curated name beats the bank's
    assert txn.description == "PayU"


def test_build_transactions_falls_back_to_the_banks_payee_without_a_rule_payee():
    rules = [Rule(pattern="PayU", category="Fun")]  # categorises, names no payee
    rows = [
        ParsedRow(date(2026, 9, 10), "PayU", Decimal("-9"), payee=" Bank name "),
        ParsedRow(date(2026, 9, 11), "Zabka", Decimal("-3"), payee="Zabka SA"),
        ParsedRow(date(2026, 9, 12), "Other", Decimal("-1")),
    ]

    txns = build_transactions(rows, "chk", rules)

    assert [t.payee for t in txns] == ["Bank name", "Zabka SA", ""]


def _payee_mapping(**columns) -> ImportMapping:
    return ImportMapping(
        bank="X",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={"date": 3, "description": 2, "amount": 1, **columns},
    )


def test_parse_rows_reads_the_payee_with_a_fallback():
    csv_text = (
        "acct;amount;description;date;merchant;recipient\n"
        "1;-1,00 PLN;PayU;11.09.2026;Cinema City;\n"
        "2;-2,00 PLN;Wire;12.09.2026;;Tauron SA\n"
        "3;-3,00 PLN;Cash;13.09.2026;;\n"
    )

    rows = parse_rows(csv_text, _payee_mapping(payee=4, payee_fallback=5))

    assert [r.payee for r in rows] == ["Cinema City", "Tauron SA", ""]
    assert rows[0].description == "PayU"  # description untouched


def test_parse_rows_without_payee_columns_has_no_payee():
    csv_text = "acct;amount;description;date\n1;-1,00 PLN;PayU;11.09.2026\n"

    assert parse_rows(csv_text, _payee_mapping())[0].payee == ""


def test_apply_payee_edits_uses_what_was_typed_including_a_cleared_box():
    rows = [{"payee": "Bank name"}, {"payee": "Other"}, {"payee": ""}]

    result = apply_payee_edits(rows, [" Cinema City ", "", "Lidl"])

    assert [r["payee"] for r in result] == ["Cinema City", "", "Lidl"]


def test_apply_payee_edits_ignores_a_list_that_does_not_line_up():
    rows = [{"payee": "Bank name"}]

    assert apply_payee_edits(rows, []) == rows
    assert apply_payee_edits(rows, ["a", "b"]) == rows


def test_build_transactions_payee_precedence_rule_then_payee_rule_then_bank():
    rules = [Rule(pattern="PayU", category="Fun", payee="Cinema City")]
    payee_rules = [
        PayeeRule(fields={DESCRIPTION_FIELD: "ZABKA Z8540 K.2"}, payee="Żabka"),
        PayeeRule(fields={DESCRIPTION_FIELD: "PayU"}, payee="Loses to the rule"),
    ]
    rows = [
        ParsedRow(date(2026, 9, 10), "PayU", Decimal("-9"), payee="Bank"),
        ParsedRow(date(2026, 9, 11), "ZABKA  Z8540 K.2", Decimal("-3"), payee="Bank"),
        ParsedRow(date(2026, 9, 12), "Other", Decimal("-1"), payee="Bank"),
    ]

    txns = build_transactions(rows, "chk", rules, payee_rules)

    assert [t.payee for t in txns] == ["Cinema City", "Żabka", "Bank"]


def test_build_transactions_matches_payee_rules_on_the_captured_fields():
    payee_rules = [
        PayeeRule(fields={DESCRIPTION_FIELD: "PayU"}, payee="PayU"),
        PayeeRule(
            fields={DESCRIPTION_FIELD: "PayU", "Web address": "cinema-city.pl"},
            payee="Cinema City",
        ),
    ]
    rows = [
        ParsedRow(
            date(2026, 9, 10),
            "PayU",
            Decimal("-9"),
            payee_fields={"Web address": "Cinema-City.pl"},
        ),
        ParsedRow(date(2026, 9, 11), "PayU", Decimal("-5")),
    ]

    txns = build_transactions(rows, "chk", [], payee_rules)

    assert [t.payee for t in txns] == ["Cinema City", "PayU"]  # more fields wins
    assert txns[0].payee_fields == {
        "Web address": "Cinema-City.pl"
    }  # stored on the row
    assert txns[1].payee_fields == {}


def test_parse_rows_captures_the_mappings_payee_field_columns_by_label():
    mapping = ImportMapping(
        bank="X",
        delimiter=";",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={"date": 3, "description": 2, "amount": 1},
        payee_fields=[
            PayeeFieldColumn(label="Web address", column=4),
            PayeeFieldColumn(label="Recipient", column=5),
            PayeeFieldColumn(label="Far away", column=99),  # beyond the row: skipped
        ],
    )
    csv_text = (
        "acct;amount;description;date;site;who\n"
        "1;-1,00 PLN;PayU;11.09.2026;cinema-city.pl;Cinema City SA\n"
        "2;-2,00 PLN;PayU;12.09.2026;;\n"
    )

    rows = parse_rows(csv_text, mapping)

    assert rows[0].payee_fields == {
        "Web address": "cinema-city.pl",
        "Recipient": "Cinema City SA",
    }
    assert rows[1].payee_fields == {}  # blank cells are left out


def test_build_transactions_records_which_import_added_each_row():
    rows = [ParsedRow(date(2026, 9, 10), "Zabka", Decimal("-3"))]

    [with_source] = build_transactions(rows, "chk", [], None, "cred")
    [without] = build_transactions(rows, "chk", [])

    assert with_source.source == "cred"
    assert without.source == ""


# ---------------------------------------------------------------------------
# Filling payee fields on existing rows from a CSV
# ---------------------------------------------------------------------------


def _row(day, desc, amount, **fields):
    return ParsedRow(date(2026, 9, day), desc, Decimal(amount), payee_fields=fields)


def test_enrichment_fills_fields_on_rows_matching_date_amount_and_text():
    ledger = [
        _txn(id="a", date=date(2026, 9, 1), description="PayU", amount=Decimal("-9")),
        _txn(id="b", date=date(2026, 9, 2), description="Zabka", amount=Decimal("-3")),
    ]
    rows = [_row(1, "payu", "-9", **{"Web address": "cinema-city.pl"})]

    plan = plan_enrichment(rows, ledger, "chk")

    assert plan.updates == {"a": {"Web address": "cinema-city.pl"}}  # text case ignored
    assert plan.unmatched == 0 and plan.ambiguous == 0 and plan.already_complete == 0


def test_enrichment_never_changes_a_saved_value_and_keeps_mapping_order():
    ledger = [
        _txn(
            id="a",
            date=date(2026, 9, 1),
            description="PayU",
            amount=Decimal("-9"),
            payee_fields={"Recipient": "Saved name"},
        )
    ]
    rows = [
        _row(1, "PayU", "-9", Recipient="Other", **{"Web address": "cinema-city.pl"})
    ]

    plan = plan_enrichment(rows, ledger, "chk")

    assert plan.updates["a"] == {
        "Recipient": "Saved name",
        "Web address": "cinema-city.pl",
    }
    assert list(plan.updates["a"]) == ["Recipient", "Web address"]  # the file's order


def test_enrichment_counts_rows_that_already_have_everything_and_unmatched_rows():
    ledger = [
        _txn(
            id="a",
            date=date(2026, 9, 1),
            description="PayU",
            amount=Decimal("-9"),
            payee_fields={"Web address": "x.pl"},
        )
    ]
    rows = [
        _row(1, "PayU", "-9", **{"Web address": "x.pl"}),
        _row(5, "Not in ledger", "-1", **{"Web address": "y.pl"}),
    ]

    plan = plan_enrichment(rows, ledger, "chk")

    assert plan.updates == {} and plan.already_complete == 1 and plan.unmatched == 1


def test_enrichment_skips_ambiguous_rows_and_other_accounts():
    ledger = [
        _txn(id="a", date=date(2026, 9, 1), description="Coffee", amount=Decimal("-4")),
        _txn(id="b", date=date(2026, 9, 1), description="Coffee", amount=Decimal("-4")),
        _txn(
            id="c",
            date=date(2026, 9, 2),
            description="Tea",
            amount=Decimal("-2"),
            account_id="other",
        ),
    ]
    rows = [
        _row(1, "Coffee", "-4", **{"Web address": "one.pl"}),
        _row(1, "Coffee", "-4", **{"Web address": "two.pl"}),  # same identity, differs
        _row(2, "Tea", "-2", **{"Web address": "tea.pl"}),
    ]

    plan = plan_enrichment(rows, ledger, "chk")

    assert plan.updates == {}
    assert plan.ambiguous == 2
    assert plan.unmatched == 1  # the Tea row belongs to another account


def test_identical_rows_with_identical_values_fill_every_matching_ledger_row():
    ledger = [
        _txn(id="a", date=date(2026, 9, 1), description="Coffee", amount=Decimal("-4")),
        _txn(id="b", date=date(2026, 9, 1), description="Coffee", amount=Decimal("-4")),
    ]
    rows = [_row(1, "Coffee", "-4", W="x")] * 2

    plan = plan_enrichment(rows, ledger, "chk")

    assert set(plan.updates) == {"a", "b"}


def test_apply_enrichment_merges_against_the_rows_as_they_are_now():
    ledger = [
        _txn(id="a", payee_fields={"W": "saved since the preview"}),
        _txn(id="b"),
    ]

    result, changed = apply_enrichment(
        ledger,
        {"a": {"W": "from the file", "X": "new"}, "b": {"W": "w"}, "ghost": {"W": "z"}},
    )

    assert changed == 2
    assert result[0].payee_fields == {"W": "saved since the preview", "X": "new"}
    assert result[1].payee_fields == {"W": "w"}


def test_merge_payee_fields_orders_by_the_wanted_labels():
    assert list(merge_payee_fields({"A": "1", "B": "2"}, {"C": "3", "B": "old"})) == [
        "A",
        "B",
        "C",
    ]
    assert merge_payee_fields({"A": "1"}, {}) == {"A": "1"}
