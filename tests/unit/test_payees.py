"""Tests for app.services.payees."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.payee import (
    COUNTERPARTY_FIELD,
    DESCRIPTION_FIELD,
    PayeeBook,
    PayeeRule,
)
from app.models.transaction import Transaction, TransactionType
from app.services.payees import (
    add_rules,
    apply_naming,
    best_rule_index,
    clean_payee_name,
    clear_skipped,
    coverage,
    default_labels,
    field_options,
    field_values,
    field_values_of,
    group_key,
    group_members,
    match_payee,
    merge_payees,
    money_flow,
    normalize_payee_fields,
    payee_summaries,
    plan_naming,
    primary_field,
    rename_payee,
    retarget_rules,
    skip_group,
    spelling_key,
    suggest_name,
    unnamed_groups,
)


def _txn(
    id_, description, *, payee="", amount="-10", day=1, **overrides
) -> Transaction:
    fields = {
        "id": id_,
        "date": date(2026, 1, day),
        "account_id": "chk",
        "category": "Food",
        "subcategory": "",
        "description": description,
        "amount": Decimal(amount),
        "type": TransactionType.EXPENSE,
        "payee": payee,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_spelling_key_ignores_case_and_spacing():
    assert spelling_key("  ZABKA   Z8540 K.2 ") == spelling_key("zabka z8540 k.2")


def test_group_key_drops_store_numbers_and_company_forms():
    assert group_key("ZABKA Z8540 K.2") == "zabka"
    assert group_key("ZABKA ZC026 K.1") == "zabka"
    assert group_key("ALLEGRO SP. Z O.O.") == "allegro"
    assert group_key("Allegro") == "allegro"


def test_group_key_keeps_the_words_that_tell_payees_apart():
    assert group_key("AUCHAN 1035 KRAKOW") == "auchan krakow"
    assert group_key("LIDL WEGLARSKA") == "lidl weglarska"


def test_group_key_splits_on_punctuation_and_drops_web_and_company_words():
    assert group_key("APPLE.COM/BILL") == "apple bill"
    assert group_key("CZAJCZYK S.C.") == "czajczyk"
    assert group_key("www.cinema-city.pl") == "cinema city"
    assert group_key("McDonald's 12") == "mcdonald"


def test_group_key_of_only_numbers_is_its_own_group():
    assert group_key("123 456") == spelling_key("123 456")


def test_suggest_name_uses_the_most_common_spelling_and_title_cases_capitals():
    assert suggest_name([("ZABKA Z8540 K.2", 389), ("ZABKA Z8540 K.1", 151)]) == "Zabka"
    assert suggest_name([("Allegro", 107), ("ALLEGRO SP. Z O.O.", 2)]) == "Allegro"
    assert suggest_name([("Lidl dakuje 256", 2)]) == "Lidl dakuje"
    assert suggest_name([("APPLE.COM/BILL", 93)]) == "Apple Bill"
    assert suggest_name([("123", 1)]) == ""
    assert suggest_name([]) == ""


def test_clean_payee_name_trims_and_validates():
    assert clean_payee_name("  Cinema   City ") == "Cinema City"
    with pytest.raises(ValueError):
        clean_payee_name("   ")
    with pytest.raises(ValueError):
        clean_payee_name("x" * 101)


def test_unnamed_groups_cluster_biggest_first_with_counts_and_totals():
    ledger = [
        _txn("1", "ZABKA Z8540 K.2", amount="-5"),
        _txn("2", "ZABKA Z8540 K.2", amount="-7", day=3),
        _txn("3", "ZABKA ZC026 K.1", amount="-3", day=2),
        _txn("4", "LIDL WEGLARSKA", amount="-100"),
    ]

    groups = unnamed_groups(ledger, PayeeBook())

    assert [g.key for g in groups] == ["zabka", "lidl weglarska"]
    zabka = groups[0]
    assert zabka.count == 3
    assert zabka.spent == Decimal("15") and zabka.received == Decimal("0")
    assert zabka.spellings == [("ZABKA Z8540 K.2", 2), ("ZABKA ZC026 K.1", 1)]
    assert zabka.suggestion == "Zabka"
    assert zabka.last_seen == date(2026, 1, 3)
    assert zabka.top_category == "Food"


def test_unnamed_groups_skip_named_transfer_blank_and_skipped_rows():
    ledger = [
        _txn("1", "ZABKA", payee="Żabka"),
        _txn("2", "TO SAVINGS", type=TransactionType.TRANSFER, transfer_id="T"),
        _txn("3", "   "),
        _txn("4", "LIDL"),
        _txn("5", "BP"),
    ]
    book = PayeeBook(skipped=["bp"])

    assert [g.key for g in unnamed_groups(ledger, book)] == ["lidl"]


def test_unnamed_groups_flag_memo_text_but_not_a_big_merchant():
    # Invoice numbers differ only by digits: one group, many one-off spellings.
    memos = [_txn(f"m{i}", f"Faktura F/{i}00/08/26") for i in range(6)]
    shop = [_txn(f"s{i}", "LIDL") for i in range(8)]

    groups = {g.key: g for g in unnamed_groups(memos + shop, PayeeBook())}

    assert groups["faktura"].memo_like is True
    assert groups["lidl"].memo_like is False


def test_rename_and_merge_change_exact_payees_and_count_rows():
    ledger = [
        _txn("1", "a", payee="Lidl"),
        _txn("2", "b", payee="LIDL Polska"),
        _txn("3", "c", payee="Zabka"),
        _txn("4", "d"),
    ]

    renamed, n = rename_payee(ledger, "Zabka", "Żabka")
    merged, m = merge_payees(ledger, ["Lidl", "LIDL Polska"], "Lidl")

    assert n == 1 and renamed[2].payee == "Żabka"
    assert m == 1  # the row already named "Lidl" is not a change
    assert [t.payee for t in merged] == ["Lidl", "Lidl", "Zabka", ""]


def test_merge_leaves_transfers_alone():
    leg = _txn("1", "x", payee="Odd", type=TransactionType.TRANSFER, transfer_id="T")

    merged, count = merge_payees([leg], ["Odd"], "New")

    assert count == 0 and merged[0].payee == "Odd"


def test_skip_and_clear_skipped():
    book = skip_group(skip_group(PayeeBook(), "zabka"), "zabka")
    assert book.skipped == ["zabka"]
    assert clear_skipped(book).skipped == []


def test_payee_summaries_report_usage_per_payee():
    ledger = [
        _txn("1", "ZABKA Z1", payee="Żabka", amount="-5", day=1),
        _txn("2", "ZABKA Z1", payee="Żabka", amount="-7", day=9, category="Snacks"),
        _txn("3", "ZABKA Z2", payee="Żabka", amount="-1", day=4),
        _txn("4", "LIDL", amount="-50"),
    ]

    [summary] = payee_summaries(ledger)

    assert summary.name == "Żabka"
    assert summary.count == 3 and summary.spent == Decimal("13")
    assert summary.received == Decimal("0")
    assert summary.top_category == "Food"
    assert summary.last_seen == date(2026, 1, 9)
    assert summary.spellings == [("ZABKA Z1", 2), ("ZABKA Z2", 1)]


def test_coverage_counts_named_over_all_non_transfer_rows():
    ledger = [
        _txn("1", "a", payee="X"),
        _txn("2", "b"),
        _txn("3", "c", type=TransactionType.TRANSFER, transfer_id="T"),
    ]

    assert coverage(ledger) == (1, 2)


def test_money_flow_keeps_spent_and_received_apart():
    rows = [
        _txn("1", "a", amount="-30"),
        _txn("2", "b", amount="-20"),
        _txn("3", "c", amount="25", type=TransactionType.INCOME),
    ]

    assert money_flow(rows) == (Decimal("50"), Decimal("25"))
    assert money_flow([]) == (Decimal("0"), Decimal("0"))


def test_a_payee_on_both_sides_reports_spent_and_received():
    ledger = [
        _txn("1", "Shop", payee="Shop", amount="-40"),
        _txn(
            "2", "Shop refund", payee="Shop", amount="15", type=TransactionType.INCOME
        ),
    ]

    [summary] = payee_summaries(ledger)

    assert summary.count == 2
    assert summary.spent == Decimal("40") and summary.received == Decimal("15")


def test_unnamed_group_reports_spent_and_received_and_ranks_by_both():
    ledger = [
        _txn("1", "ACME", amount="-10"),
        _txn("2", "ACME", amount="90", type=TransactionType.INCOME),
    ]

    [group] = unnamed_groups(ledger, PayeeBook())

    assert group.spent == Decimal("10") and group.received == Decimal("90")


# ---------------------------------------------------------------------------
# Fields, rules and naming plans
# ---------------------------------------------------------------------------


def _web(id_, description, site, **kw):
    return _txn(id_, description, payee_fields={"Web address": site}, **kw)


def test_field_values_lists_built_ins_then_labelled_fields_and_drops_blanks():
    txn = _txn(
        "1",
        "PayU",
        counterparty_account="PL61",
        payee_fields={"Web address": "cinema-city.pl", "Recipient": "  "},
    )

    assert field_values(txn) == {
        DESCRIPTION_FIELD: "PayU",
        COUNTERPARTY_FIELD: "PL61",
        "Web address": "cinema-city.pl",
    }
    assert field_values_of("", "", {}) == {}


def test_a_rule_needs_every_field_and_ignores_case_and_spacing():
    rule = PayeeRule(
        fields={DESCRIPTION_FIELD: "PAYU", "Web address": "Cinema-City.pl"}, payee="X"
    )
    ok = {DESCRIPTION_FIELD: " payu ", "Web address": "cinema-city.pl"}

    assert match_payee([rule], ok) == "X"
    assert match_payee([rule], {DESCRIPTION_FIELD: "PayU"}) is None  # field missing
    assert match_payee([rule], {**ok, "Web address": "other.pl"}) is None


def test_the_rule_with_more_fields_wins_then_the_newer_one():
    broad = PayeeRule(fields={DESCRIPTION_FIELD: "PayU"}, payee="PayU")
    narrow = PayeeRule(
        fields={DESCRIPTION_FIELD: "PayU", "Web address": "cinema-city.pl"},
        payee="Cinema City",
    )
    newer_broad = PayeeRule(fields={DESCRIPTION_FIELD: "PayU"}, payee="Newer PayU")
    values = {DESCRIPTION_FIELD: "PayU", "Web address": "cinema-city.pl"}

    assert match_payee([broad, narrow], values) == "Cinema City"
    assert (
        match_payee([narrow, broad], values) == "Cinema City"
    )  # order doesn't beat specificity
    assert (
        match_payee([broad, newer_broad], {DESCRIPTION_FIELD: "PayU"}) == "Newer PayU"
    )
    assert best_rule_index([broad], {DESCRIPTION_FIELD: "nope"}) is None


def test_add_rules_replaces_a_rule_with_the_same_condition():
    book = PayeeBook(
        rules=[PayeeRule(fields={DESCRIPTION_FIELD: "ZABKA"}, payee="Old")]
    )

    book = add_rules(
        book, [PayeeRule(fields={DESCRIPTION_FIELD: " zabka "}, payee="Żabka")]
    )

    assert [r.payee for r in book.rules] == ["Żabka"]


def test_normalize_payee_fields_keeps_five_unique_labels_in_priority_order():
    fields = normalize_payee_fields(
        [
            ("Web", 89),
            ("", 15),
            ("Description", 10),
            ("web", 5),
            ("Title", 17),
            ("Sixth", 1),
        ]
    )

    assert [(f.label, f.column) for f in fields] == [
        ("Web", 89),
        ("Field 2", 15),
        ("Description 2", 10),
        ("web 2", 5),
        ("Title", 17),
    ]  # a sixth is dropped


def test_groups_form_on_the_first_captured_field_else_the_description():
    ledger = [
        _web("1", "PayU", "www.cinema-city.pl"),
        _web("2", "PayU", "www.cinema-city.pl"),
        _web("3", "PayU", "www.allegro.pl"),
        _txn("4", "ZABKA Z1"),
    ]

    groups = {g.key: g for g in unnamed_groups(ledger, PayeeBook())}

    assert set(groups) == {"cinema city", "allegro", "zabka"}
    assert groups["cinema city"].primary_label == "Web address"
    assert groups["cinema city"].spellings == [("www.cinema-city.pl", 2)]
    assert groups["zabka"].primary_label == DESCRIPTION_FIELD
    assert primary_field(ledger[0]) == ("Web address", "www.cinema-city.pl")


def test_field_options_summarise_each_field_over_the_group():
    members = [
        _web("1", "PayU", "cinema-city.pl", counterparty_account=""),
        _web("2", "PayU 2", "cinema-city.pl"),
        _txn("3", "PayU"),
    ]

    options = {o.label: o for o in field_options(members)}

    assert list(options) == [
        DESCRIPTION_FIELD,
        "Web address",
    ]  # no counterparty anywhere
    assert options[DESCRIPTION_FIELD].present == 3
    assert options["Web address"].present == 2 and options["Web address"].total == 3
    assert options["Web address"].values == [("cinema-city.pl", 2)]
    assert default_labels(members) == ["Web address"]
    assert default_labels([_txn("9", "x")]) == [DESCRIPTION_FIELD]


def test_plan_naming_makes_one_rule_per_distinct_value_combination():
    ledger = [
        _txn("1", "ZABKA Z1"),
        _txn("2", "ZABKA Z1"),
        _txn("3", "ZABKA Z2"),
        _txn("4", "LIDL"),
    ]

    plan = plan_naming(ledger, PayeeBook(), "zabka", [DESCRIPTION_FIELD], "Żabka")

    assert sorted(r.fields[DESCRIPTION_FIELD] for r in plan.rules) == [
        "ZABKA Z1",
        "ZABKA Z2",
    ]
    assert [t.id for t in plan.rows] == ["1", "2", "3"]
    named = apply_naming(ledger, plan, "Żabka")
    assert [t.payee for t in named] == ["Żabka", "Żabka", "Żabka", ""]
    assert [t.description for t in named] == [t.description for t in ledger]


def test_plan_naming_on_a_web_address_names_rows_in_other_groups_too():
    ledger = [
        _web("1", "PayU", "cinema-city.pl"),
        _web("2", "Przelewy24", "cinema-city.pl"),  # a different description
        _web("3", "PayU", "allegro.pl"),
    ]

    plan = plan_naming(
        ledger, PayeeBook(), "cinema city", ["Web address"], "Cinema City"
    )

    assert [r.fields for r in plan.rules] == [{"Web address": "cinema-city.pl"}]
    assert [t.id for t in plan.rows] == ["1", "2"]


def test_plan_naming_leaves_group_rows_lacking_a_chosen_field_unnamed():
    # Both rows group as "payu": one through its web address, one by its description.
    ledger = [_web("1", "PayU", "payu.pl"), _txn("2", "PayU")]

    plan = plan_naming(ledger, PayeeBook(), "payu", ["Web address"], "PayU")

    assert plan.rows_without_fields == 1
    assert [t.id for t in plan.rows] == ["1"]


def test_plan_naming_never_overwrites_a_payee_or_touches_a_transfer():
    ledger = [
        _txn("1", "ZABKA"),
        _txn("2", "ZABKA", payee="Keep me"),
        _txn("3", "ZABKA", type=TransactionType.TRANSFER, transfer_id="T"),
    ]

    plan = plan_naming(ledger, PayeeBook(), "zabka", [DESCRIPTION_FIELD], "Żabka")

    assert [t.id for t in plan.rows] == ["1"]
    assert group_members(ledger, "zabka") == [ledger[0]]


def test_plan_naming_leaves_a_row_to_an_older_more_specific_rule():
    book = PayeeBook(
        rules=[
            PayeeRule(
                fields={DESCRIPTION_FIELD: "PayU", "Web address": "cinema-city.pl"},
                payee="Cinema City",
            )
        ]
    )
    ledger = [
        _web("1", "PayU", "cinema-city.pl"),
        _web("2", "PayU", "other.pl"),
    ]

    # Name the "other" group by description: the new broad "PayU" rule would match
    # row 1 too, but the older rule on two fields beats it, so only row 2 is named.
    plan = plan_naming(ledger, book, "other", [DESCRIPTION_FIELD], "PayU")

    assert [t.id for t in plan.rows] == ["2"]


def test_plan_naming_with_no_labels_does_nothing():
    plan = plan_naming([_txn("1", "ZABKA")], PayeeBook(), "zabka", [], "Żabka")

    assert plan.rules == [] and plan.rows == []


def test_retarget_rules_follows_a_rename():
    book = PayeeBook(
        rules=[
            PayeeRule(fields={DESCRIPTION_FIELD: "A"}, payee="Lidl"),
            PayeeRule(fields={DESCRIPTION_FIELD: "B"}, payee="Zabka"),
        ]
    )

    book = retarget_rules(book, ["Lidl"], "Lidl Polska")

    assert [r.payee for r in book.rules] == ["Lidl Polska", "Zabka"]
