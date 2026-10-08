"""Tests for the 50/30/20 aggregation in app.services.aggregation."""

from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountStatus, AccountType
from app.models.transaction import Transaction, TransactionType
from app.services.aggregation import (
    budget_rule_monthly_series,
    budget_rule_split,
    top_unclassified_spend,
)

CATEGORIES = {
    "income": {
        "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
        "Refunds": {
            "icon": "",
            "budget": "",
            "bucket": "excluded",
            "subcategories": {},
        },
    },
    "expense": {
        "Rent": {"icon": "", "budget": "", "bucket": "need", "subcategories": {}},
        "Food": {
            "icon": "",
            "budget": "",
            "bucket": "need",
            "subcategories": {
                "Cafes": {"icon": "", "budget": "", "bucket": "want"},
                "Groceries": {"icon": "", "budget": "", "bucket": ""},
            },
        },
        "Fun": {"icon": "", "budget": "", "bucket": "want", "subcategories": {}},
        "Misc": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
    },
}


def _acct(id: str, kind: AccountType, status=AccountStatus.ACTIVE) -> Account:
    return Account(
        id=id,
        name=id,
        number=id,
        starting_balance=Decimal("0"),
        account_type=kind,
        status=status,
    )


ACCOUNTS = [
    _acct("chk", AccountType.CHECKING),
    _acct("sav", AccountType.SAVINGS),
    _acct("old", AccountType.SAVINGS, AccountStatus.CLOSED),
    _acct("inv", AccountType.INVESTMENT),
]


def _t(amount, type_, category="", subcategory="", account="chk", day=5, month=3):
    return Transaction(
        id=f"{type_.value}-{amount}-{category}-{subcategory}-{account}-{day}-{month}",
        date=date(2026, month, day),
        account_id=account,
        category=category,
        subcategory=subcategory,
        description="d",
        amount=Decimal(amount),
        type=type_,
    )


E, INC, T = TransactionType.EXPENSE, TransactionType.INCOME, TransactionType.TRANSFER


def test_split_buckets_and_percentages():
    txns = [
        _t("1000", INC, "Salary"),
        _t("-500", E, "Rent"),
        _t("-100", E, "Food", "Cafes"),  # sub override -> want
        _t("-50", E, "Food", "Groceries"),  # blank sub -> inherits need
        _t("-50", E, "Food"),  # no sub -> need
        _t("-150", E, "Fun"),
        _t("-50", E, "Misc"),  # unclassified
        _t("200", T, account="sav"),
        _t("-200", T, account="chk"),
    ]

    split = budget_rule_split(txns, CATEGORIES, ACCOUNTS)

    assert split.income == Decimal("1000")
    assert (split.needs, split.wants, split.unclassified) == (
        Decimal("600"),
        Decimal("250"),
        Decimal("50"),
    )
    assert split.savings == Decimal("200")
    assert split.needs_pct == Decimal("60.0")
    assert split.wants_pct == Decimal("25.0")
    assert split.unclassified_pct == Decimal("5.0")
    assert split.savings_pct == Decimal("20.0")


def test_excluded_income_is_left_out_and_unknown_category_counts():
    txns = [
        _t("1000", INC, "Salary"),
        _t("300", INC, "Refunds"),
        _t("100", INC, "Gone"),  # not in tree -> counts
    ]
    assert budget_rule_split(txns, CATEGORIES, ACCOUNTS).income == Decimal("1100")


def test_refund_nets_against_spend_and_unknown_expense_is_unclassified():
    txns = [_t("-100", E, "Fun"), _t("30", E, "Fun"), _t("-20", E, "Nope")]
    split = budget_rule_split(txns, CATEGORIES, ACCOUNTS)
    assert split.wants == Decimal("70")
    assert split.unclassified == Decimal("20")


def test_savings_withdrawals_reduce_and_internal_moves_cancel():
    txns = [
        _t("500", T, account="sav"),
        _t("-200", T, account="sav"),  # withdrawal
        _t("300", T, account="old"),  # closed savings still counts
        _t("-300", T, account="sav"),  # savings -> savings pair nets out...
        _t("1000", T, account="inv"),  # investment never counts
        _t("-1000", T, account="chk"),
    ]
    split = budget_rule_split(txns, CATEGORIES, ACCOUNTS)
    assert split.savings == Decimal("300")


def test_no_income_gives_amounts_only():
    split = budget_rule_split([_t("-100", E, "Rent")], CATEGORIES, ACCOUNTS)
    assert split.needs == Decimal("100")
    assert split.needs_pct is None and split.savings_pct is None


def test_negative_savings_shows_negative_percentage():
    txns = [_t("1000", INC, "Salary"), _t("-100", T, account="sav")]
    assert budget_rule_split(txns, CATEGORIES, ACCOUNTS).savings_pct == Decimal("-10.0")


def test_period_bounds_are_inclusive():
    txns = [
        _t("-10", E, "Rent", day=1),
        _t("-20", E, "Rent", day=15),
        _t("-40", E, "Rent", day=31),
    ]
    split = budget_rule_split(
        txns, CATEGORIES, ACCOUNTS, date(2026, 3, 15), date(2026, 3, 31)
    )
    assert split.needs == Decimal("60")


def test_monthly_series_covers_empty_months_through_today():
    txns = [_t("-10", E, "Rent", month=1), _t("-30", E, "Rent", month=3)]

    series = budget_rule_monthly_series(txns, CATEGORIES, ACCOUNTS, date(2026, 4, 10))

    assert [m.key for m in series] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert [m.split.needs for m in series] == [
        Decimal("10"),
        Decimal("0"),
        Decimal("30"),
        Decimal("0"),
    ]


def test_monthly_series_empty_ledger():
    assert budget_rule_monthly_series([], CATEGORIES, ACCOUNTS, date(2026, 4, 1)) == []


def test_top_unclassified_spend_ranks_by_total_and_skips_classified():
    txns = [
        _t("-10", E, "Misc"),
        _t("-90", E, "Mystery"),
        _t("-5", E, "Mystery"),
        _t("-500", E, "Rent"),
    ]
    top = top_unclassified_spend(txns, CATEGORIES, limit=5)
    assert [(u.category, u.total, u.count) for u in top] == [
        ("Mystery", Decimal("95"), 2),
        ("Misc", Decimal("10"), 1),
    ]
