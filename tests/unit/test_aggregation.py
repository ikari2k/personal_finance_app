"""Tests for app.services.aggregation."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType
from app.services.aggregation import (
    category_breakdown,
    category_monthly_totals,
    group_by_month_and_type,
    grouped_transaction_view,
    merge_months,
    monthly_totals_with_mom,
    net_worth_by_month,
    subcategory_monthly_totals,
    yearly_totals_with_yoy,
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


def _account(**overrides) -> Account:
    fields = {
        "id": "chk",
        "name": "Checking",
        "number": "1234",
        "description": "",
        "starting_balance": Decimal("0"),
    }
    fields.update(overrides)
    return Account(**fields)


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


def test_yearly_totals_with_yoy_returns_newest_first():
    txns = [
        _txn(
            id="t1",
            date=date(2025, 1, 1),
            type=TransactionType.INCOME,
            amount=Decimal("100"),
        ),
        _txn(
            id="t2",
            date=date(2026, 1, 1),
            type=TransactionType.INCOME,
            amount=Decimal("150"),
        ),
    ]

    years = yearly_totals_with_yoy(txns)

    assert [y.year for y in years] == [2026, 2025]


def test_yearly_totals_with_yoy_computes_income_expense_and_net():
    txns = [
        _txn(id="t1", type=TransactionType.INCOME, amount=Decimal("100")),
        _txn(id="t2", type=TransactionType.EXPENSE, amount=Decimal("-40")),
    ]

    [year] = yearly_totals_with_yoy(txns)

    assert year.income_total == Decimal("100")
    assert year.expense_total == Decimal("-40")
    assert year.net_total == Decimal("60")


def test_yearly_totals_with_yoy_first_year_has_no_delta():
    txns = [_txn(type=TransactionType.INCOME, amount=Decimal("100"))]

    [year] = yearly_totals_with_yoy(txns)

    assert year.yoy_delta is None


def test_yearly_totals_with_yoy_computes_delta_against_prior_year():
    txns = [
        _txn(
            id="t1",
            date=date(2025, 1, 1),
            type=TransactionType.INCOME,
            amount=Decimal("100"),
        ),
        _txn(
            id="t2",
            date=date(2026, 1, 1),
            type=TransactionType.INCOME,
            amount=Decimal("150"),
        ),
    ]

    years = yearly_totals_with_yoy(txns)
    current = next(y for y in years if y.year == 2026)

    assert current.yoy_delta == Decimal("50")


def test_yearly_totals_with_yoy_excludes_transfers():
    txns = [
        _txn(
            id="t1",
            type=TransactionType.TRANSFER,
            category="Transfer",
            amount=Decimal("500"),
        ),
    ]

    years = yearly_totals_with_yoy(txns)

    assert years == []


def test_monthly_totals_with_mom_returns_newest_first():
    txns = [
        _txn(id="t1", date=date(2026, 7, 1)),
        _txn(id="t2", date=date(2026, 9, 1)),
    ]

    months = monthly_totals_with_mom(txns)

    assert [m.key for m in months] == ["2026-09", "2026-07"]


def test_monthly_totals_with_mom_first_month_has_no_delta():
    txns = [_txn(date=date(2026, 8, 1))]

    [month] = monthly_totals_with_mom(txns)

    assert month.mom_delta is None


def test_monthly_totals_with_mom_computes_delta_against_prior_month():
    txns = [
        _txn(id="t1", date=date(2026, 7, 1), amount=Decimal("-10")),
        _txn(id="t2", date=date(2026, 8, 1), amount=Decimal("-30")),
    ]

    months = monthly_totals_with_mom(txns)
    august = next(m for m in months if m.key == "2026-08")

    assert august.mom_delta == Decimal("-20")


def test_monthly_totals_with_mom_delta_chains_across_a_year_boundary():
    txns = [
        _txn(id="t1", date=date(2025, 12, 1), amount=Decimal("-10")),
        _txn(id="t2", date=date(2026, 1, 1), amount=Decimal("-25")),
    ]

    months = monthly_totals_with_mom(txns)
    january = next(m for m in months if m.key == "2026-01")

    assert january.mom_delta == Decimal("-15")


def test_category_breakdown_raises_for_transfer_type():
    with pytest.raises(ValueError):
        category_breakdown([], 2026, TransactionType.TRANSFER)


def test_category_breakdown_groups_by_category_and_subcategory():
    txns = [
        _txn(
            id="t1",
            category="Groceries",
            subcategory="Supermarket",
            amount=Decimal("-50"),
        ),
        _txn(
            id="t2",
            category="Groceries",
            subcategory="Supermarket",
            amount=Decimal("-20"),
        ),
        _txn(
            id="t3",
            category="Groceries",
            subcategory="Farmers Market",
            amount=Decimal("-15"),
        ),
    ]

    [groceries] = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert groceries.name == "Groceries"
    assert groceries.total == Decimal("85")
    assert groceries.count == 3
    assert {s.name: s.total for s in groceries.subcategories} == {
        "Supermarket": Decimal("70"),
        "Farmers Market": Decimal("15"),
    }
    assert {s.name: s.count for s in groceries.subcategories} == {
        "Supermarket": 2,
        "Farmers Market": 1,
    }


def test_category_breakdown_counts_blank_subcategory_toward_total_without_a_row():
    txns = [_txn(category="Health", subcategory="", amount=Decimal("-30"))]

    [health] = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert health.total == Decimal("30")
    assert health.count == 1
    assert health.subcategories == []


def test_category_breakdown_sorted_by_total_descending():
    txns = [
        _txn(id="t1", category="Small", amount=Decimal("-5")),
        _txn(id="t2", category="Big", amount=Decimal("-500")),
    ]

    breakdown = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert [c.name for c in breakdown] == ["Big", "Small"]


def test_category_breakdown_ignores_other_years_and_types():
    txns = [
        _txn(
            id="t1", date=date(2025, 1, 1), category="Groceries", amount=Decimal("-50")
        ),
        _txn(
            id="t2",
            type=TransactionType.INCOME,
            category="Salary",
            amount=Decimal("100"),
        ),
    ]

    breakdown = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert breakdown == []


def test_category_breakdown_yoy_delta_against_prior_year_same_category():
    txns = [
        _txn(
            id="t1", date=date(2025, 1, 1), category="Groceries", amount=Decimal("-50")
        ),
        _txn(
            id="t2", date=date(2026, 1, 1), category="Groceries", amount=Decimal("-80")
        ),
    ]

    [groceries] = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert groceries.yoy_delta == Decimal("30")


def test_category_breakdown_yoy_delta_is_none_for_a_new_category():
    txns = [_txn(category="Groceries", amount=Decimal("-50"))]

    [groceries] = category_breakdown(txns, 2026, TransactionType.EXPENSE)

    assert groceries.yoy_delta is None


def test_category_monthly_totals_raises_for_transfer_type():
    with pytest.raises(ValueError):
        category_monthly_totals([], 2026, TransactionType.TRANSFER)


def test_category_monthly_totals_groups_by_category_and_month():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 1, 5),
            category="Groceries",
            amount=Decimal("-50"),
        ),
        _txn(
            id="t2",
            date=date(2026, 1, 20),
            category="Groceries",
            amount=Decimal("-20"),
        ),
        _txn(
            id="t3",
            date=date(2026, 2, 5),
            category="Groceries",
            amount=Decimal("-30"),
        ),
    ]

    totals = category_monthly_totals(txns, 2026, TransactionType.EXPENSE)

    assert totals == {"Groceries": {"2026-01": Decimal("70"), "2026-02": Decimal("30")}}


def test_category_monthly_totals_omits_a_month_with_no_activity():
    txns = [_txn(date=date(2026, 1, 5), category="Groceries", amount=Decimal("-50"))]

    totals = category_monthly_totals(txns, 2026, TransactionType.EXPENSE)

    assert "2026-02" not in totals["Groceries"]


def test_category_monthly_totals_ignores_other_years_and_types():
    txns = [
        _txn(
            id="t1",
            date=date(2025, 1, 5),
            category="Groceries",
            amount=Decimal("-50"),
        ),
        _txn(
            id="t2",
            type=TransactionType.INCOME,
            category="Salary",
            amount=Decimal("100"),
        ),
    ]

    totals = category_monthly_totals(txns, 2026, TransactionType.EXPENSE)

    assert totals == {}


def test_subcategory_monthly_totals_raises_for_transfer_type():
    with pytest.raises(ValueError):
        subcategory_monthly_totals([], 2026, TransactionType.TRANSFER)


def test_subcategory_monthly_totals_groups_by_category_subcategory_and_month():
    txns = [
        _txn(
            id="t1",
            date=date(2026, 1, 5),
            category="Groceries",
            subcategory="Supermarket",
            amount=Decimal("-50"),
        ),
        _txn(
            id="t2",
            date=date(2026, 2, 5),
            category="Groceries",
            subcategory="Supermarket",
            amount=Decimal("-30"),
        ),
        _txn(
            id="t3",
            date=date(2026, 1, 10),
            category="Groceries",
            subcategory="Farmers Market",
            amount=Decimal("-15"),
        ),
    ]

    totals = subcategory_monthly_totals(txns, 2026, TransactionType.EXPENSE)

    assert totals == {
        "Groceries": {
            "Supermarket": {"2026-01": Decimal("50"), "2026-02": Decimal("30")},
            "Farmers Market": {"2026-01": Decimal("15")},
        }
    }


def test_subcategory_monthly_totals_includes_blank_subcategory_key():
    txns = [_txn(date=date(2026, 1, 5), category="Health", amount=Decimal("-30"))]

    totals = subcategory_monthly_totals(txns, 2026, TransactionType.EXPENSE)

    assert totals == {"Health": {"": {"2026-01": Decimal("30")}}}


def test_net_worth_by_month_starts_from_combined_starting_balances():
    accounts = [_account(id="chk", starting_balance=Decimal("100"))]

    points = net_worth_by_month([], accounts)

    assert points == []


def test_net_worth_by_month_is_oldest_first_and_cumulative():
    accounts = [_account(id="chk", starting_balance=Decimal("1000"))]
    txns = [
        _txn(
            id="t1",
            date=date(2026, 7, 1),
            type=TransactionType.INCOME,
            amount=Decimal("200"),
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 1),
            type=TransactionType.EXPENSE,
            amount=Decimal("-50"),
        ),
    ]

    points = net_worth_by_month(txns, accounts)

    assert [p.key for p in points] == ["2026-07", "2026-08"]
    assert points[0].value == Decimal("1200")
    assert points[1].value == Decimal("1150")


def test_net_worth_by_month_transfer_legs_cancel_out():
    accounts = [
        _account(id="chk", starting_balance=Decimal("500")),
        _account(id="sav", starting_balance=Decimal("500")),
    ]
    txns = [
        _txn(
            id="t1",
            date=date(2026, 8, 1),
            account_id="chk",
            type=TransactionType.TRANSFER,
            category="Transfer",
            amount=Decimal("-100"),
            transfer_id="tr1",
        ),
        _txn(
            id="t2",
            date=date(2026, 8, 1),
            account_id="sav",
            type=TransactionType.TRANSFER,
            category="Transfer",
            amount=Decimal("100"),
            transfer_id="tr1",
        ),
    ]

    [point] = net_worth_by_month(txns, accounts)

    assert point.value == Decimal("1000")
