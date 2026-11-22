"""Tests for app.services.payees."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.payee import PayeeAlias, PayeeBook
from app.models.transaction import Transaction, TransactionType
from app.services.payees import (
    add_aliases,
    alias_lookup,
    clean_payee_name,
    clear_skipped,
    coverage,
    group_key,
    group_members,
    merge_payees,
    money_flow,
    name_group,
    payee_summaries,
    rename_payee,
    retarget_aliases,
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


def test_name_group_names_only_payeeless_rows_of_that_group():
    ledger = [
        _txn("1", "ZABKA Z1 K.1"),
        _txn("2", "ZABKA Z2 K.2"),
        _txn("3", "ZABKA Z3", payee="Already named"),
        _txn("4", "LIDL"),
    ]

    updated, count = name_group(ledger, "zabka", "Żabka")

    assert count == 2
    assert [t.payee for t in updated] == ["Żabka", "Żabka", "Already named", ""]
    assert [t.description for t in updated] == [t.description for t in ledger]


def test_name_group_never_touches_transfers():
    ledger = [_txn("1", "ZABKA", type=TransactionType.TRANSFER, transfer_id="T")]

    updated, count = name_group(ledger, "zabka", "Żabka")

    assert count == 0 and updated[0].payee == ""
    assert group_members(ledger, "zabka") == []


def test_add_aliases_remembers_spellings_and_repoints_existing_ones():
    book = PayeeBook(aliases=[PayeeAlias(spelling="ZABKA Z1", payee="Old")])

    book = add_aliases(book, ["zabka  z1", "ZABKA Z2"], "Żabka")

    assert {a.payee for a in book.aliases} == {"Żabka"}
    assert len(book.aliases) == 2
    assert alias_lookup(book) == {"zabka z1": "Żabka", "zabka z2": "Żabka"}


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


def test_retarget_aliases_follows_a_rename():
    book = PayeeBook(
        aliases=[
            PayeeAlias(spelling="A", payee="Lidl"),
            PayeeAlias(spelling="B", payee="Zabka"),
        ]
    )

    book = retarget_aliases(book, ["Lidl"], "Lidl Polska")

    assert [a.payee for a in book.aliases] == ["Lidl Polska", "Zabka"]


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
