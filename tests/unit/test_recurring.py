"""Tests for app.services.recurring and app.storage.expense_nature."""

from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction, TransactionType
from app.services.recurring import (
    NATURE_FIXED,
    NATURE_FLEXIBLE,
    NATURE_SEMI,
    detect_recurring,
    merchant_key,
    suggest_natures,
)
from app.storage.expense_nature import read_nature_tags, write_nature_tags

TODAY = date(2026, 11, 10)


def _txn(day, amount, description, category="c", subcategory="", **kw) -> Transaction:
    fields = {
        "id": f"{description}-{day}-{amount}",
        "date": day,
        "account_id": "chk",
        "category": category,
        "subcategory": subcategory,
        "description": description,
        "amount": Decimal(amount),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(kw)
    return Transaction(**fields)


def _monthly(description, amounts, *, day=5, category="c", subcategory=""):
    """One charge a month ending in Nov 2026, one per entry of ``amounts``."""
    out = []
    for i, amount in enumerate(reversed(amounts)):
        month = 11 - i
        year = 2026
        while month < 1:
            month += 12
            year -= 1
        out.append(
            _txn(date(year, month, day), amount, description, category, subcategory)
        )
    return out


def _verdict(groups, key):
    return next(g.verdict for g in groups if g.key == key)


def test_merchant_key_drops_numbers_and_punctuation():
    assert merchant_key("ZABKA Z123 K.1") == merchant_key("ZABKA ZA9 K.2") == "ZABKA"
    assert merchant_key("Orange PL 2026/03") == "ORANGE PL"


def test_steady_monthly_charge_is_fixed():
    rent = _monthly("LANDLORD RENT", ["-3200"] * 12)

    [group] = detect_recurring(rent, TODAY)

    assert group.verdict == NATURE_FIXED
    assert group.period == 30
    assert group.months_present == 12
    assert group.variation == 0
    assert group.confidence >= 90


def test_regular_but_variable_charge_is_semi_fixed():
    bills = _monthly("ELECTRICITY", ["-300", "-380", "-250", "-330", "-290", "-400"])

    assert _verdict(detect_recurring(bills, TODAY), "ELECTRICITY") == NATURE_SEMI


def test_irregular_charges_are_flexible():
    days = [3, 4, 9, 15, 16, 22, 28]
    shops = [_txn(date(2026, 10, d), str(-20 - d * 3), "SUPERMARKET") for d in days]

    [group] = detect_recurring(shops, TODAY)

    assert group.verdict == NATURE_FLEXIBLE
    assert group.period is None


def test_yearly_charge_needs_only_two_occurrences():
    insurance = [
        _txn(date(2025, 3, 10), "-1800", "CAR INSURANCE"),
        _txn(date(2026, 3, 12), "-1840", "CAR INSURANCE"),
    ]

    [group] = detect_recurring(insurance, TODAY)

    assert group.period == 365
    assert group.verdict == NATURE_FIXED


def test_two_monthly_charges_are_not_enough_to_call_regular():
    two = _monthly("NEW SUBSCRIPTION", ["-40", "-40"])

    assert detect_recurring(two, TODAY)[0].verdict == NATURE_FLEXIBLE


def test_store_variants_merge_into_one_group():
    txns = _monthly("GYM CLUB 12", ["-189"] * 6) + _monthly(
        "GYM CLUB 98", ["-189"] * 6, day=6
    )

    groups = detect_recurring(txns, TODAY)

    assert len(groups) == 1
    assert groups[0].variants == ["GYM CLUB 12", "GYM CLUB 98"]


def test_income_transfers_and_stale_groups_are_ignored():
    txns = [
        _txn(date(2026, 10, 1), "100", "SALARY", type=TransactionType.INCOME),
        _txn(date(2026, 10, 2), "-50", "MOVE", type=TransactionType.TRANSFER),
        _txn(date(2024, 1, 2), "-9", "OLD THING"),
    ]
    txns.append(_txn(date(2026, 10, 3), "-5", "LIVE"))

    assert [g.key for g in detect_recurring(txns, TODAY)] == ["LIVE"]
    assert detect_recurring([], TODAY) == []


def test_short_history_does_not_penalise_presence():
    # Ledger only 4 months old: 4 of 4 months present counts as full coverage.
    rent = _monthly("RENT", ["-1000"] * 4)

    assert detect_recurring(rent, TODAY)[0].verdict == NATURE_FIXED


def test_suggest_natures_uses_dominant_spend_else_semi():
    rent = _monthly("RENT", ["-3000"] * 12, category="Housing", subcategory="Rent")
    repairs = [_txn(date(2026, 9, 1), "-100", "PLUMBER", "Housing", "Rent")]
    food = _monthly("SHOP A", ["-500"] * 12, category="Food", subcategory="Shops") + [
        _txn(date(2026, 10, d), "-60", "SHOP B", "Food", "Shops") for d in (1, 9, 20)
    ]

    suggestions = suggest_natures(detect_recurring(rent + repairs + food, TODAY))

    assert suggestions[("Housing", "Rent")] == NATURE_FIXED  # 3000*12 vs 100
    assert suggestions[("Food", "Shops")] == NATURE_FIXED


def test_nature_tags_round_trip_and_skip_unknown(tmp_path):
    path = tmp_path / "expense_nature.toml"
    assert read_nature_tags(path) == {}
    tags = {("Housing", ""): "fixed", ("Food", "Cafe"): "flexible"}

    write_nature_tags(tags, path)
    assert read_nature_tags(path) == tags

    path.write_text(path.read_text().replace("flexible", "bogus"))
    assert read_nature_tags(path) == {("Housing", ""): "fixed"}


def test_write_nature_tags_leaves_a_backup(tmp_path):
    path = tmp_path / "expense_nature.toml"
    write_nature_tags({("A", ""): "fixed"}, path)
    write_nature_tags({("A", ""): "semi"}, path)

    assert (tmp_path / "expense_nature.toml.bak").exists()
    assert read_nature_tags(path) == {("A", ""): "semi"}
