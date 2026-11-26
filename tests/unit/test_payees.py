"""Tests for app.services.payees."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.payee import (
    DESCRIPTION_FIELD,
    PayeeBook,
    PayeeRule,
)
from app.models.transaction import Transaction, TransactionType
from app.services.payees import (
    add_rules,
    apply_naming,
    base_group_key,
    best_rule_index,
    clean_payee_name,
    clear_skipped,
    combinations,
    coverage,
    default_labels,
    dismissed_ids,
    field_options,
    field_text,
    field_values,
    field_values_of,
    group_key,
    group_members,
    has_any_payee_field,
    make_key,
    match_payee,
    merge_payees,
    money_flow,
    normalize_payee_fields,
    payee_field_labels,
    payee_summaries,
    plan_naming,
    rename_payee,
    retarget_rules,
    row_group_key,
    skip_group,
    spelling_key,
    split_group,
    split_key,
    suggest_name,
    unnamed_groups,
    unsplit_group,
    without_payee_fields,
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
        # A row as a mapping with one payee field ("Place") would import it.
        "payee_fields": {"Place": description} if description.strip() else {},
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

    groups = unnamed_groups(ledger, PayeeBook(), "Place")

    assert [g.key for g in groups] == ["Place::zabka", "Place::lidl weglarska"]
    zabka = groups[0]
    assert zabka.label == "Place"
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
    book = PayeeBook(skipped=["Place::bp"])

    assert [g.key for g in unnamed_groups(ledger, book, "Place")] == ["Place::lidl"]


def test_unnamed_groups_flag_memo_text_but_not_a_big_merchant():
    # Invoice numbers differ only by digits: one group, many one-off spellings.
    memos = [_txn(f"m{i}", f"Faktura F/{i}00/08/26") for i in range(6)]
    shop = [_txn(f"s{i}", "LIDL") for i in range(8)]

    groups = {g.key: g for g in unnamed_groups(memos + shop, PayeeBook(), "Place")}

    assert groups["Place::faktura"].memo_like is True
    assert groups["Place::lidl"].memo_like is False


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

    [group] = unnamed_groups(ledger, PayeeBook(), "Place")

    assert group.spent == Decimal("10") and group.received == Decimal("90")


def _web(id_, description, site, **kw):
    return _txn(id_, description, payee_fields={"Web address": site}, **kw)


def test_field_values_are_the_payee_fields_only_without_blanks():
    txn = _txn(
        "1",
        "PayU",
        counterparty_account="PL61",
        payee_fields={"Web address": "cinema-city.pl", "Recipient": "  "},
    )

    # the description joins as its own field; the counterparty account never does
    assert field_values(txn) == {"Web address": "cinema-city.pl", "Description": "PayU"}
    assert field_values_of({"Web address": "cinema-city.pl"}) == {
        "Web address": "cinema-city.pl"
    }
    assert field_values_of({}) == {}


def test_a_description_that_repeats_a_payee_field_is_not_a_field_of_its_own():
    txn = _txn("1", "Cinema City", payee_fields={"Place": "CINEMA  city"})

    assert field_values(txn) == {"Place": "CINEMA  city"}
    assert field_values(_txn("2", "  ", payee_fields={})) == {}


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


def test_each_payee_field_has_its_own_suggestions():
    ledger = [
        _card("1", "www.cinema-city.pl"),
        _card("2", "www.cinema-city.pl"),
        _card("3", "www.allegro.pl"),
    ]

    by_merchant = unnamed_groups(ledger, PayeeBook(), "Merchant")
    by_site = unnamed_groups(ledger, PayeeBook(), "Web address")

    assert [(g.key, g.count) for g in by_merchant] == [("Merchant::payu", 3)]
    assert [(g.key, g.count) for g in by_site] == [
        ("Web address::cinema city", 2),
        ("Web address::allegro", 1),
    ]
    # the same row is offered under both fields until it is named
    assert {t.id for t in group_members(ledger, "Merchant::payu")} == {"1", "2", "3"}
    assert {t.id for t in group_members(ledger, "Web address::allegro")} == {"3"}


def test_a_field_a_row_lacks_gives_it_no_suggestion_under_that_field():
    ledger = [
        _card("1", "allegro.pl"),
        _txn("2", "PayU", payee_fields={"Merchant": "PayU"}),
    ]

    by_site = unnamed_groups(ledger, PayeeBook(), "Web address")

    assert [(g.key, g.count) for g in by_site] == [("Web address::allegro", 1)]


def test_a_group_reports_how_many_rows_also_have_each_other_field():
    ledger = [
        _card("1", "allegro.pl", Title="Order 1"),
        _card("2", "allegro.pl"),
        _txn("3", "PayU", payee_fields={"Merchant": "PayU"}),
    ]

    [group] = unnamed_groups(ledger, PayeeBook(), "Merchant")

    assert group.count == 3
    assert group.other_fields == [("Web address", 2), ("Title", 1)]
    assert group.has_more_detail


def test_naming_a_row_removes_it_from_every_fields_suggestions():
    ledger = [_card("1", "allegro.pl"), _card("2", "cinema-city.pl")]
    plan = plan_naming(
        ledger, PayeeBook(), "Web address::allegro", ["Web address"], "Allegro"
    )
    ledger = apply_naming(ledger, plan, "Allegro")

    assert [
        (g.key, g.count) for g in unnamed_groups(ledger, PayeeBook(), "Merchant")
    ] == [("Merchant::payu", 1)]
    assert [g.key for g in unnamed_groups(ledger, PayeeBook(), "Web address")] == [
        "Web address::cinema city"
    ]


def test_rows_without_any_payee_field_are_not_grouped_and_are_counted():
    ledger = [
        _txn("1", "ZABKA Z1", payee_fields={}),
        _txn("2", "LIDL", payee_fields={"Place": " "}),
        _txn("3", "ACME"),  # has a field
        _txn("4", "OLD", payee="Named", payee_fields={}),  # named: not counted
        _txn(
            "5",
            "TO SAVINGS",
            type=TransactionType.TRANSFER,
            transfer_id="T",
            payee_fields={},
        ),
    ]

    assert [g.key for g in unnamed_groups(ledger, PayeeBook(), "Place")] == [
        "Place::acme"
    ]
    assert without_payee_fields(ledger) == 2
    assert group_members(ledger, "Place::zabka") == []
    assert has_any_payee_field(ledger[2]) and not has_any_payee_field(ledger[0])


def test_field_options_offer_only_the_payee_fields_the_rows_have():
    members = [
        _card("1", "cinema-city.pl").model_copy(
            update={"counterparty_account": "PL61"}
        ),
        _card("2", "cinema-city.pl"),
    ]

    options = {o.label: o for o in field_options(members)}

    # the counterparty account and the description are never offered
    assert list(options) == ["Merchant", "Web address"]
    assert options["Web address"].present == 2 and options["Web address"].total == 2
    assert options["Web address"].values == [("cinema-city.pl", 2)]
    assert default_labels("Merchant::payu", members) == ["Merchant"]


def test_the_default_rule_field_is_the_one_the_group_is_built_from():
    members = [
        _txn("1", "A", payee_fields={"Place": "ACME"}),
        _txn("2", "B", payee_fields={"Place": "ACME", "Web address": "acme.pl"}),
    ]

    assert default_labels("Place::acme", members) == ["Place"]
    assert default_labels("Web address::acme", members) == ["Web address"]
    assert default_labels("Title::acme", members) == []  # no row has that field
    assert default_labels("no separator", members) == []


def test_field_options_are_empty_for_rows_with_no_payee_field():
    assert field_options([_txn("1", "", payee_fields={})]) == []


def test_an_old_row_with_only_a_description_is_grouped_by_description():
    old = [
        _txn("1", "ZABKA Z1", payee_fields={}),
        _txn("2", "ZABKA Z2", payee_fields={}),
    ]

    [group] = unnamed_groups(old, PayeeBook(), "Description")

    assert group.count == 2
    assert group.key == "Description::zabka"


def test_a_row_lacking_the_payee_field_is_not_part_of_the_group():
    members = [
        _txn("1", "CZAJCZYK S.C."),
        _txn("2", "CZAJCZYK S.C."),
        _txn("3", "CZAJCZYK S.C.", payee_fields={}),  # no field: cannot be grouped
    ]

    [group] = unnamed_groups(members, PayeeBook(), "Place")

    assert group.count == 2
    assert [o.label for o in field_options(group_members(members, group.key))] == [
        "Place"
    ]


def test_plan_naming_makes_one_rule_per_distinct_value_combination():
    ledger = [
        _txn("1", "ZABKA Z1"),
        _txn("2", "ZABKA Z1"),
        _txn("3", "ZABKA Z2"),
        _txn("4", "LIDL"),
    ]

    plan = plan_naming(ledger, PayeeBook(), "Place::zabka", ["Place"], "Żabka")

    assert sorted(r.fields["Place"] for r in plan.rules) == ["ZABKA Z1", "ZABKA Z2"]
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
        ledger, PayeeBook(), "Web address::cinema city", ["Web address"], "Cinema City"
    )

    assert [r.fields for r in plan.rules] == [{"Web address": "cinema-city.pl"}]
    assert [t.id for t in plan.rows] == ["1", "2"]


def test_plan_naming_leaves_group_rows_lacking_a_chosen_field_unnamed():
    # Both rows are in the "Merchant: PayU" group; only one has a web address.
    ledger = [
        _card("1", "payu.pl"),
        _txn("2", "PayU", payee_fields={"Merchant": "PayU"}),
    ]

    plan = plan_naming(ledger, PayeeBook(), "Merchant::payu", ["Web address"], "PayU")

    assert plan.rows_without_fields == 1
    assert [t.id for t in plan.rows] == ["1"]


def test_plan_naming_never_overwrites_a_payee_or_touches_a_transfer():
    ledger = [
        _txn("1", "ZABKA"),
        _txn("2", "ZABKA", payee="Keep me"),
        _txn("3", "ZABKA", type=TransactionType.TRANSFER, transfer_id="T"),
    ]

    plan = plan_naming(ledger, PayeeBook(), "Place::zabka", ["Place"], "Żabka")

    assert [t.id for t in plan.rows] == ["1"]
    assert group_members(ledger, "Place::zabka") == [ledger[0]]


def test_plan_naming_leaves_a_row_to_an_older_more_specific_rule():
    book = PayeeBook(
        rules=[
            PayeeRule(
                fields={"Merchant": "PayU", "Web address": "cinema-city.pl"},
                payee="Cinema City",
            )
        ]
    )
    ledger = [_card("1", "cinema-city.pl"), _card("2", "other.pl")]

    # Name the whole "payu" group on Merchant alone: the new broad rule would match
    # row 1 too, but the older rule on two fields beats it, so only row 2 is named.
    plan = plan_naming(ledger, book, "Merchant::payu", ["Merchant"], "PayU")

    assert [t.id for t in plan.rows] == ["2"]


def test_plan_naming_with_no_labels_does_nothing():
    plan = plan_naming([_txn("1", "ZABKA")], PayeeBook(), "Place::zabka", [], "Żabka")

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


# ---------------------------------------------------------------------------
# Splitting a group by more than one column
# ---------------------------------------------------------------------------


def _card(id_, site, amount="-10", **fields):
    return _txn(
        id_,
        "PayU",
        amount=amount,
        payee_fields={"Merchant": "PayU", "Web address": site, **fields},
    )


def test_combinations_split_a_group_by_the_ticked_columns_biggest_first():
    members = [
        _card("1", "cinema-city.pl", "-30"),
        _card("2", "Cinema-City.pl", "-20"),
        _card("3", "allegro.pl", "-5"),
        _card("4", "allegro.pl", "-5", Title="Order 9"),
    ]

    combos, without = combinations(members, ["Merchant", "Web address"])

    assert without == 0
    assert [(c.values["Web address"], c.count) for c in combos] == [
        ("cinema-city.pl", 2),  # case-insensitive, spelled as most often written
        ("allegro.pl", 2),
    ]
    assert combos[0].spent == Decimal("50") and combos[0].received == Decimal("0")


def test_combinations_keep_the_other_configured_columns_visible():
    members = [
        _card("1", "allegro.pl", Title="Order 1"),
        _card("2", "allegro.pl", Title="Order 1"),
    ]

    [combo], _ = combinations(members, ["Web address"])

    # the built-in bank text is not listed; the unticked payee fields are
    assert combo.others == {"Merchant": "PayU", "Title": "Order 1"}


def test_combinations_count_rows_missing_a_ticked_column():
    members = [_card("1", "allegro.pl"), _txn("2", "PayU")]

    combos, without = combinations(members, ["Web address"])

    assert len(combos) == 1 and without == 1
    assert combinations(members, []) == ([], 2)


def test_plan_naming_only_names_one_combination_with_one_rule():
    ledger = [
        _card("1", "cinema-city.pl"),
        _card("2", "cinema-city.pl"),
        _card("3", "allegro.pl"),
    ]

    plan = plan_naming(
        ledger,
        PayeeBook(),
        "Merchant::payu",
        ["ignored"],
        "Cinema City",
        only={"Merchant": "payu", "Web address": "Cinema-City.pl"},
    )

    assert [r.fields for r in plan.rules] == [
        {"Merchant": "PayU", "Web address": "cinema-city.pl"}
    ]
    assert [t.id for t in plan.rows] == ["1", "2"]  # allegro.pl is left alone


def test_naming_two_combinations_of_one_group_as_different_payees():
    ledger = [_card("1", "cinema-city.pl"), _card("2", "allegro.pl")]
    book = PayeeBook()
    first = plan_naming(
        ledger,
        book,
        "Merchant::payu",
        [],
        "Cinema City",
        only={"Web address": "cinema-city.pl"},
    )
    ledger = apply_naming(ledger, first, "Cinema City")
    book = add_rules(book, first.rules)

    second = plan_naming(
        ledger,
        book,
        "Merchant::payu",
        [],
        "Allegro",
        only={"Web address": "allegro.pl"},
    )

    assert [t.id for t in second.rows] == ["2"]  # the first one stays Cinema City
    assert [t.payee for t in ledger] == ["Cinema City", ""]


def _zabkas():
    return [
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "zabka  z8540 k.2"),  # same spelling apart from case and spacing
        _txn("3", "ZABKA Z8540 K.1"),
        _txn("4", "LIDL"),
    ]


def test_an_undivided_cluster_lists_all_its_spellings_in_one_group():
    groups = {g.key: g for g in unnamed_groups(_zabkas(), PayeeBook(), "Place")}

    # "ZABKA Z8540 K.2" and "zabka  z8540 k.2": one spelling (case, spacing ignored)
    assert groups["Place::zabka"].spellings == [
        ("ZABKA Z8540 K.2", 2),
        ("ZABKA Z8540 K.1", 1),
    ]
    assert not groups["Place::zabka"].exact


def test_dividing_makes_one_group_per_exact_spelling():
    book = split_group(PayeeBook(), "Place::zabka")

    groups = unnamed_groups(_zabkas(), book, "Place")

    exact = [g for g in groups if g.exact]
    assert sorted(g.key for g in exact) == [
        "Place::exact:zabka z8540 k.1",
        "Place::exact:zabka z8540 k.2",
    ]
    assert all(len(g.spellings) == 1 and g.base_key == "Place::zabka" for g in exact)
    assert {g.key: g.count for g in exact}["Place::exact:zabka z8540 k.2"] == 2
    assert [g.key for g in groups if not g.exact] == ["Place::lidl"]  # others untouched


def test_dividing_a_group_under_one_field_leaves_other_fields_alone():
    ledger = [
        _card("1", "cinema-city.pl"),
        _card("2", "allegro.pl"),
    ]
    book = split_group(PayeeBook(), "Merchant::payu")

    by_merchant = unnamed_groups(ledger, book, "Merchant")
    by_site = unnamed_groups(ledger, book, "Web address")

    assert [g.exact for g in by_merchant] == [True]  # "PayU" is one spelling anyway
    assert not any(g.exact for g in by_site)  # the division belongs to Merchant only


def test_group_members_understands_exact_keys():
    ledger = _zabkas()

    members = group_members(ledger, "Place::exact:zabka z8540 k.2")

    assert [t.id for t in members] == ["1", "2"]
    assert group_members(ledger, "Place::exact:nothing here") == []
    assert group_members(ledger, "zabka") == []  # a key without a field names nothing


def test_row_group_key_and_base_key():
    txn = _txn("1", "ZABKA Z8540 K.2")

    assert row_group_key(txn, "Place", set()) == "Place::zabka"
    assert (
        row_group_key(txn, "Place", {"Place::zabka"}) == "Place::exact:zabka z8540 k.2"
    )
    assert row_group_key(txn, "Place", {"Web address::zabka"}) == "Place::zabka"
    assert base_group_key("Place::exact:zabka z8540 k.2") == "Place::zabka"
    assert base_group_key("Place::zabka") == "Place::zabka"


def test_keys_carry_the_field_they_belong_to():
    assert make_key("Web address", "allegro") == "Web address::allegro"
    assert split_key("Web address::allegro") == ("Web address", "allegro")
    assert split_key("Place::exact:a::b") == ("Place", "exact:a::b")  # first separator
    assert split_key("legacy") == ("", "legacy")
    assert field_text(_txn("1", "x", payee_fields={"A": "  a   b "}), "A") == "a b"
    assert field_text(_txn("1", "x", payee_fields={}), "A") == ""


def test_split_is_idempotent_resolves_exact_keys_and_can_be_undone():
    book = split_group(
        split_group(PayeeBook(), "Place::zabka"), "Place::exact:zabka z8540 k.1"
    )

    assert book.split == ["Place::zabka"]  # an exact key means its cluster, once
    assert unsplit_group(book, "Place::exact:zabka z8540 k.2").split == []
    assert unsplit_group(PayeeBook(), "Place::zabka").split == []


def test_a_divided_spelling_can_be_planned_and_skipped_on_its_own():
    ledger = _zabkas()
    plan = plan_naming(
        ledger, PayeeBook(), "Place::exact:zabka z8540 k.2", ["Place"], "Rynek"
    )

    assert [t.id for t in plan.rows] == ["1", "2"]
    skipped = skip_group(
        split_group(PayeeBook(), "Place::zabka"), "Place::exact:zabka z8540 k.1"
    )
    keys = [g.key for g in unnamed_groups(ledger, skipped, "Place")]
    assert "Place::exact:zabka z8540 k.1" not in keys
    assert "Place::exact:zabka z8540 k.2" in keys


def test_distinct_spellings_ignore_case_and_spacing_and_show_the_commonest_form():
    from app.services.payees import distinct_spellings

    result = distinct_spellings(["Allegro", "ALLEGRO", "Allegro", " allegro ", "Lidl"])

    assert result == [("Allegro", 4), ("Lidl", 1)]
    assert distinct_spellings([]) == []


def test_group_properties_decide_whether_a_workspace_is_worth_opening():
    one_field = [_txn("1", "CZAJCZYK", payee_fields={"Place": "CZAJCZYK"})]
    two_fields = [_card("2", "allegro.pl")]  # Merchant + Web address
    many = [_txn(str(i), f"SHOP {i}00 K.{i}") for i in range(4)]
    three = [_txn(str(i), f"HALL {i}00 K.{i}") for i in range(3)]

    def groups(ledger, label):
        return {g.key: g for g in unnamed_groups(ledger, PayeeBook(), label)}

    assert not groups(one_field, "Place")["Place::czajczyk"].has_more_detail
    assert groups(two_fields, "Merchant")[
        "Merchant::payu"
    ].has_more_detail  # other field
    assert groups(many, "Place")["Place::shop"].has_more_detail  # 4 > 3 spellings
    assert not groups(three, "Place")["Place::hall"].has_more_detail  # card shows all


def test_payee_field_labels_list_the_configured_ones_first_then_stray_ones_by_use():
    ledger = [
        _txn("1", "a", payee_fields={"Web address": "x", "Old field": "y"}),
        _txn("2", "b", payee_fields={"Old field": "y", "Other": "z"}),
        _txn("3", "c", payee_fields={"Old field": "y"}),
    ]

    labels = payee_field_labels(ledger, ["Web address", "Place", "Web address"])

    # the description is always offered, last
    assert labels == ["Web address", "Place", "Old field", "Other", "Description"]
    assert payee_field_labels([], []) == ["Description"]


def test_labels_cannot_contain_the_key_separator():
    [field] = normalize_payee_fields([("A::B", 1)])

    assert field.label == "A:B"


def test_dismissing_a_group_hides_its_rows_under_every_field():
    shop = _txn(
        "1", "ZABKA Z1", payee_fields={"Place": "ZABKA Z1", "Seller": "Zabka Sp"}
    )
    other = _txn("2", "IKEA", payee_fields={"Place": "IKEA", "Seller": "Ikea Sp"})
    book = skip_group(PayeeBook(), "Place::" + group_key("ZABKA Z1"))

    assert {g.label for g in unnamed_groups([shop, other], book, "Seller")} == {
        "Seller"
    }
    sellers = unnamed_groups([shop, other], book, "Seller")
    assert [t for g in sellers for t in g.spellings] == [("Ikea Sp", 1)]
    assert dismissed_ids([shop, other], book) == {"1"}
    # Nothing was named and no rule was made.
    assert shop.payee == "" and book.rules == []
