"""Tests for app.services.aggregation."""

from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction, TransactionType
from app.services.aggregation import (
    group_by_month_and_type,
    grouped_transaction_view,
    merge_months,
)


def _txn(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 8, 1),
        "account_id": "chk",
        "category": "c",
        "subcategory": "",
        "description": "",
        "amount": Decimal("-10"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_empty_ledger_returns_no_months():
    assert group_by_month_and_type([]) == []


def test_groups_by_month_newest_first():
    txns = [
        _txn(id="t1", date=date(2026, 7, 1)),
        _txn(id="t2", date=date(2026, 9, 1)),
        _txn(id="t3", date=date(2026, 8, 1)),
    ]

    months = group_by_month_and_type(txns)

    assert [m.label for m in months] == ["September 2026", "August 2026", "July 2026"]


def test_only_present_types_appear_and_transactions_sort_newest_first():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 8, 1),
            type=TransactionType.EXPENSE,
            amount=Decimal("-10"),
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 15),
            type=TransactionType.EXPENSE,
            amount=Decimal("-5"),
        ),
    ]

    months = group_by_month_and_type(txns)

    assert len(months) == 1
    assert [g.type for g in months[0].groups] == [TransactionType.EXPENSE]
    assert [t.id for t in months[0].groups[0].transactions] == ["t2", "t1"]


def test_type_groups_are_ordered_expense_income_transfer():
    txns = [
        _txn(id="t1", type=TransactionType.INCOME, amount=Decimal("100")),
        _txn(
            id="t2",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            account_id="chk",
            amount=Decimal("-50"),
        ),
        _txn(
            id="t3",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            account_id="sav",
            amount=Decimal("50"),
        ),
        _txn(id="t4", type=TransactionType.EXPENSE, amount=Decimal("-10")),
    ]

    months = group_by_month_and_type(txns)

    assert [g.type for g in months[0].groups] == [
        TransactionType.EXPENSE,
        TransactionType.INCOME,
        TransactionType.TRANSFER,
    ]


def test_income_and_expense_subtotals_are_signed_sums():
    txns = [
        _txn(id="t1", type=TransactionType.INCOME, amount=Decimal("3200")),
        _txn(id="t2", type=TransactionType.INCOME, amount=Decimal("450")),
        _txn(id="t3", type=TransactionType.EXPENSE, amount=Decimal("-1500")),
        _txn(id="t4", type=TransactionType.EXPENSE, amount=Decimal("-100")),
    ]

    months = group_by_month_and_type(txns)

    subtotals = {g.type: g.subtotal for g in months[0].groups}
    assert subtotals[TransactionType.INCOME] == Decimal("3650")
    assert subtotals[TransactionType.EXPENSE] == Decimal("-1600")


def test_transfer_subtotal_is_volume_moved_not_a_zero_net():
    txns = [
        _txn(
            id="t1",
            account_id="chk",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            amount=Decimal("-500"),
        ),
        _txn(
            id="t2",
            account_id="sav",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            amount=Decimal("500"),
        ),
    ]

    months = group_by_month_and_type(txns)

    transfer_group = next(
        g for g in months[0].groups if g.type == TransactionType.TRANSFER
    )
    assert transfer_group.subtotal == Decimal("500")


def test_net_total_excludes_transfers():
    txns = [
        _txn(id="t1", type=TransactionType.INCOME, amount=Decimal("1000")),
        _txn(id="t2", type=TransactionType.EXPENSE, amount=Decimal("-300")),
        _txn(
            id="t3",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            account_id="chk",
            amount=Decimal("-500"),
        ),
        _txn(
            id="t4",
            type=TransactionType.TRANSFER,
            transfer_id="x1",
            account_id="sav",
            amount=Decimal("500"),
        ),
    ]

    months = group_by_month_and_type(txns)

    assert months[0].net_total == Decimal("700")


def test_month_group_carries_a_flat_transactions_list_too():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 8, 1),
            type=TransactionType.INCOME,
            amount=Decimal("100"),
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 15),
            type=TransactionType.EXPENSE,
            amount=Decimal("-10"),
        ),
    ]

    months = group_by_month_and_type(txns)

    assert [t.id for t in months[0].transactions] == ["t2", "t1"]


def test_merge_months_of_empty_ledger_is_empty():
    assert merge_months([]) == []


def test_merge_months_collapses_into_one_pseudo_month_newest_first():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 7, 1),
            type=TransactionType.INCOME,
            amount=Decimal("100"),
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 1),
            type=TransactionType.INCOME,
            amount=Decimal("200"),
        ),
    ]
    months = group_by_month_and_type(txns)

    merged = merge_months(months)

    assert len(merged) == 1
    assert [t.id for t in merged[0].transactions] == ["t2", "t1"]
    assert merged[0].net_total == Decimal("300")


def test_merge_months_resums_type_subtotals_across_months():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 7, 1),
            type=TransactionType.EXPENSE,
            amount=Decimal("-10"),
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 1),
            type=TransactionType.EXPENSE,
            amount=Decimal("-5"),
        ),
        _txn(
            id="t3",
            date=date(2026, 8, 2),
            type=TransactionType.INCOME,
            amount=Decimal("100"),
        ),
    ]
    months = group_by_month_and_type(txns)

    merged = merge_months(months)

    subtotals = {g.type: g.subtotal for g in merged[0].groups}
    assert subtotals[TransactionType.EXPENSE] == Decimal("-15")
    assert subtotals[TransactionType.INCOME] == Decimal("100")


def test_grouped_transaction_view_both_on_matches_group_by_month_and_type():
    txns = [
        _txn(id="t1"),
        _txn(id="t2", type=TransactionType.INCOME, amount=Decimal("5")),
    ]

    assert grouped_transaction_view(
        txns, by_month=True, by_type=True
    ) == group_by_month_and_type(txns)


def test_grouped_transaction_view_month_off_merges_months():
    txns = [
        _txn(id="t1", date=date(2026, 7, 1)),
        _txn(id="t2", date=date(2026, 8, 1)),
    ]

    view = grouped_transaction_view(txns, by_month=False, by_type=True)

    assert len(view) == 1
    assert view[0].key == "all"


def test_grouped_transaction_view_type_off_clears_groups_but_keeps_transactions():
    txns = [
        _txn(id="t1"),
        _txn(id="t2", type=TransactionType.INCOME, amount=Decimal("5")),
    ]

    view = grouped_transaction_view(txns, by_month=True, by_type=False)

    assert view[0].groups == []
    assert len(view[0].transactions) == 2


def test_grouped_transaction_view_both_off_is_one_flat_group():
    txns = [
        _txn(id="t1", date=date(2026, 7, 1)),
        _txn(
            id="t2",
            date=date(2026, 8, 1),
            type=TransactionType.INCOME,
            amount=Decimal("5"),
        ),
    ]

    view = grouped_transaction_view(txns, by_month=False, by_type=False)

    assert len(view) == 1
    assert view[0].groups == []
    assert [t.id for t in view[0].transactions] == ["t2", "t1"]
