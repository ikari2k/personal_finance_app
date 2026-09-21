"""Tests for app.services.transactions business rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType
from app.services.transactions import (
    OrphanTransferCandidate,
    TransferMatch,
    apply_transfer_matches,
    ensure_category,
    find_orphan_transfer_candidates,
    find_transfer_matches,
    merge_into_transfer,
    new_transaction,
    new_transfer_pair,
    remove_transaction,
    synthesize_and_merge_transfer,
    update_transaction,
    update_transaction_category,
    update_transaction_notes,
    update_transfer_pair,
)

ACCOUNT_IDS = ["chk", "sav"]


def _txn(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 1, 1),
        "account_id": "chk",
        "category": "Groceries",
        "subcategory": "Supermarket",
        "description": "x",
        "amount": Decimal("-10"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_ensure_category_adds_new_category_and_subcategory():
    result = ensure_category({}, TransactionType.EXPENSE, "Groceries", "Supermarket")

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        }
    }


def test_ensure_category_does_not_duplicate_existing_subcategory():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "cart",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "store", "budget": ""}},
            }
        }
    }

    result = ensure_category(
        existing, TransactionType.EXPENSE, "Groceries", "Supermarket"
    )

    assert result == existing


def test_ensure_category_does_not_mutate_the_input():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        }
    }

    ensure_category(existing, TransactionType.EXPENSE, "Groceries", "Restaurants")

    assert existing == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        }
    }


def test_ensure_category_skips_blank_subcategory():
    result = ensure_category({}, TransactionType.EXPENSE, "Groceries", "")

    assert result == {
        "expense": {"Groceries": {"icon": "", "budget": "", "subcategories": {}}}
    }


def test_ensure_category_keeps_income_and_expense_trees_separate():
    existing = {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        }
    }

    result = ensure_category(existing, TransactionType.INCOME, "Salary", "")

    assert result == {
        "expense": {
            "Groceries": {
                "icon": "",
                "budget": "",
                "subcategories": {"Supermarket": {"icon": "", "budget": ""}},
            }
        },
        "income": {"Salary": {"icon": "", "budget": "", "subcategories": {}}},
    }


def test_ensure_category_rejects_transfer_type():
    with pytest.raises(ValueError):
        ensure_category({}, TransactionType.TRANSFER, "Transfer", "")


def test_new_transaction_normalizes_expense_to_negative():
    txn = new_transaction(
        ACCOUNT_IDS,
        account_id="chk",
        date=date(2026, 1, 1),
        category="Groceries",
        subcategory="Supermarket",
        description="x",
        amount=Decimal("42.50"),
        type=TransactionType.EXPENSE,
    )

    assert txn.amount == Decimal("-42.50")


def test_new_transaction_keeps_income_positive():
    txn = new_transaction(
        ACCOUNT_IDS,
        account_id="chk",
        date=date(2026, 1, 1),
        category="Income",
        subcategory="Salary",
        description="x",
        amount=Decimal("1000"),
        type=TransactionType.INCOME,
    )

    assert txn.amount == Decimal("1000")


def test_new_transaction_rejects_unknown_account():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="ghost",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_new_transaction_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("0"),
            type=TransactionType.EXPENSE,
        )


def test_new_transaction_rejects_transfer_type():
    with pytest.raises(ValueError):
        new_transaction(
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.TRANSFER,
        )


def test_new_transfer_pair_builds_opposite_sign_rows_sharing_transfer_id():
    outflow, inflow = new_transfer_pair(
        ACCOUNT_IDS,
        from_account_id="chk",
        to_account_id="sav",
        date=date(2026, 1, 1),
        amount=Decimal("100"),
    )

    assert outflow.transfer_id == inflow.transfer_id
    assert outflow.amount == Decimal("-100")
    assert inflow.amount == Decimal("100")
    assert outflow.account_id == "chk"
    assert inflow.account_id == "sav"
    assert outflow.type is TransactionType.TRANSFER
    assert inflow.type is TransactionType.TRANSFER


def test_new_transfer_pair_rejects_same_account_on_both_sides():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="chk",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_new_transfer_pair_rejects_unknown_account():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="ghost",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_new_transfer_pair_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        new_transfer_pair(
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="sav",
            date=date(2026, 1, 1),
            amount=Decimal("0"),
        )


def test_update_transaction_replaces_fields_but_keeps_the_id():
    existing = [_txn(id="t1", description="old")]

    updated = update_transaction(
        existing,
        "t1",
        ACCOUNT_IDS,
        account_id="sav",
        date=date(2026, 2, 2),
        category="Rent",
        subcategory="",
        description="new",
        amount=Decimal("20"),
        type=TransactionType.EXPENSE,
    )

    assert len(updated) == 1
    assert updated[0].id == "t1"
    assert updated[0].account_id == "sav"
    assert updated[0].description == "new"
    assert updated[0].amount == Decimal("-20")


def test_update_transaction_rejects_unknown_id():
    with pytest.raises(ValueError):
        update_transaction(
            [_txn(id="t1")],
            "ghost",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_update_transaction_rejects_editing_a_transfer_leg():
    transfer_leg = _txn(id="t1", type=TransactionType.TRANSFER, transfer_id="x1")

    with pytest.raises(ValueError):
        update_transaction(
            [transfer_leg],
            "t1",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.EXPENSE,
        )


def test_update_transaction_rejects_changing_type_to_transfer():
    with pytest.raises(ValueError):
        update_transaction(
            [_txn(id="t1")],
            "t1",
            ACCOUNT_IDS,
            account_id="chk",
            date=date(2026, 1, 1),
            category="c",
            subcategory="",
            description="",
            amount=Decimal("10"),
            type=TransactionType.TRANSFER,
        )


def test_update_transaction_category_changes_only_category_fields():
    existing = _txn(
        id="t1",
        account_id="chk",
        date=date(2026, 3, 4),
        category="old",
        subcategory="old-sub",
        description="Coffee",
        amount=Decimal("-4.50"),
        notes="keep me",
    )

    [updated] = update_transaction_category(
        [existing], "t1", category="new", subcategory="new-sub"
    )

    assert updated.category == "new"
    assert updated.subcategory == "new-sub"
    assert updated.account_id == "chk"
    assert updated.date == date(2026, 3, 4)
    assert updated.description == "Coffee"
    assert updated.amount == Decimal("-4.50")
    assert updated.notes == "keep me"


def test_update_transaction_category_rejects_unknown_id():
    with pytest.raises(ValueError):
        update_transaction_category(
            [_txn(id="t1")], "ghost", category="new", subcategory=""
        )


def test_update_transaction_category_rejects_a_transfer_leg():
    transfer_leg = _txn(id="t1", type=TransactionType.TRANSFER, transfer_id="x1")

    with pytest.raises(ValueError):
        update_transaction_category(
            [transfer_leg], "t1", category="new", subcategory=""
        )


def test_update_transaction_notes_changes_only_notes():
    existing = _txn(
        id="t1",
        category="Groceries",
        subcategory="Supermarket",
        description="Lidl 4471",
        notes=None,
    )

    [updated] = update_transaction_notes([existing], "t1", notes="weekly shop")

    assert updated.notes == "weekly shop"
    assert updated.description == "Lidl 4471"
    assert updated.category == "Groceries"
    assert updated.subcategory == "Supermarket"


def test_update_transaction_notes_empty_string_clears_to_none():
    existing = _txn(id="t1", notes="old note")

    [updated] = update_transaction_notes([existing], "t1", notes="")

    assert updated.notes is None


def test_update_transaction_notes_allowed_on_a_transfer_leg():
    transfer_leg = _txn(id="t1", type=TransactionType.TRANSFER, transfer_id="x1")

    [updated] = update_transaction_notes([transfer_leg], "t1", notes="moving funds")

    assert updated.notes == "moving funds"


def test_update_transaction_notes_rejects_unknown_id():
    with pytest.raises(ValueError):
        update_transaction_notes([_txn(id="t1")], "ghost", notes="x")


def test_remove_transaction_deletes_a_plain_row():
    existing = [_txn(id="t1"), _txn(id="t2")]

    result = remove_transaction(existing, "t1")

    assert [t.id for t in result] == ["t2"]


def test_remove_transaction_rejects_unknown_id():
    with pytest.raises(ValueError):
        remove_transaction([_txn(id="t1")], "ghost")


def test_remove_transaction_deletes_both_legs_of_a_transfer():
    outflow = _txn(
        id="t1", account_id="chk", type=TransactionType.TRANSFER, transfer_id="x1"
    )
    inflow = _txn(
        id="t2", account_id="sav", type=TransactionType.TRANSFER, transfer_id="x1"
    )
    other = _txn(id="t3")

    result = remove_transaction([outflow, inflow, other], "t1")

    assert [t.id for t in result] == ["t3"]


def _transfer_pair(transfer_id="x1", amount=Decimal("-100"), **overrides):
    outflow = _txn(
        id="t1",
        account_id="chk",
        amount=amount,
        type=TransactionType.TRANSFER,
        category="Transfer",
        subcategory="",
        transfer_id=transfer_id,
        **overrides,
    )
    inflow = _txn(
        id="t2",
        account_id="sav",
        amount=-amount,
        type=TransactionType.TRANSFER,
        category="Transfer",
        subcategory="",
        transfer_id=transfer_id,
        **overrides,
    )
    return outflow, inflow


def test_update_transfer_pair_replaces_both_legs_keeping_ids():
    outflow, inflow = _transfer_pair()
    other = _txn(id="t3")

    result = update_transfer_pair(
        [outflow, inflow, other],
        "x1",
        ["chk", "sav", "cc"],
        from_account_id="sav",
        to_account_id="cc",
        date=date(2026, 3, 3),
        amount=Decimal("250"),
        description="renamed",
        notes="edited",
    )

    by_id = {t.id: t for t in result}
    assert len(result) == 3
    assert by_id["t1"].account_id == "sav"
    assert by_id["t1"].amount == Decimal("-250")
    assert by_id["t2"].account_id == "cc"
    assert by_id["t2"].amount == Decimal("250")
    assert by_id["t1"].transfer_id == "x1"
    assert by_id["t2"].transfer_id == "x1"
    assert by_id["t1"].description == "renamed"
    assert by_id["t2"].notes == "edited"
    assert by_id["t3"] is other


def test_update_transfer_pair_rejects_unknown_transfer_id():
    outflow, inflow = _transfer_pair()

    with pytest.raises(ValueError):
        update_transfer_pair(
            [outflow, inflow],
            "ghost",
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="sav",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_update_transfer_pair_rejects_same_account_on_both_sides():
    outflow, inflow = _transfer_pair()

    with pytest.raises(ValueError):
        update_transfer_pair(
            [outflow, inflow],
            "x1",
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="chk",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_update_transfer_pair_rejects_unknown_account():
    outflow, inflow = _transfer_pair()

    with pytest.raises(ValueError):
        update_transfer_pair(
            [outflow, inflow],
            "x1",
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="ghost",
            date=date(2026, 1, 1),
            amount=Decimal("10"),
        )


def test_update_transfer_pair_rejects_non_positive_amount():
    outflow, inflow = _transfer_pair()

    with pytest.raises(ValueError):
        update_transfer_pair(
            [outflow, inflow],
            "x1",
            ACCOUNT_IDS,
            from_account_id="chk",
            to_account_id="sav",
            date=date(2026, 1, 1),
            amount=Decimal("0"),
        )


ACCOUNTS = [
    Account(id="chk", name="Checking", number="111 222", starting_balance=Decimal("0")),
    Account(id="sav", name="Savings", number="333 444", starting_balance=Decimal("0")),
    Account(id="cc", name="Card", number="555 666", starting_balance=Decimal("0")),
]


def test_find_transfer_matches_pairs_by_counterparty_account():
    out = _txn(
        id="a",
        account_id="chk",
        amount=Decimal("-50"),
        counterparty_account="333444",
    )
    inn = _txn(id="b", account_id="sav", amount=Decimal("50"))

    [match] = find_transfer_matches([out, inn], ACCOUNTS)

    assert match.from_transaction_id == "a"
    assert match.to_transaction_id == "b"
    assert match.from_account_id == "chk"
    assert match.to_account_id == "sav"
    assert match.amount == Decimal("50")


def test_find_transfer_matches_respects_max_day_gap():
    out = _txn(
        id="a",
        account_id="chk",
        date=date(2026, 1, 1),
        amount=Decimal("-50"),
        counterparty_account="333444",
    )
    inn = _txn(id="b", account_id="sav", date=date(2026, 1, 10), amount=Decimal("50"))

    assert find_transfer_matches([out, inn], ACCOUNTS) == []
    [match] = find_transfer_matches([out, inn], ACCOUNTS, max_day_gap=30)
    assert match.from_transaction_id == "a"


def test_find_transfer_matches_ignores_unregistered_counterparty():
    out = _txn(
        id="a", account_id="chk", amount=Decimal("-50"), counterparty_account="999999"
    )
    inn = _txn(id="b", account_id="sav", amount=Decimal("50"))

    assert find_transfer_matches([out, inn], ACCOUNTS) == []


def test_find_transfer_matches_skips_existing_transfer_rows():
    out = _txn(
        id="a",
        account_id="chk",
        amount=Decimal("-50"),
        counterparty_account="333444",
        type=TransactionType.TRANSFER,
        transfer_id="existing",
    )
    inn = _txn(
        id="b",
        account_id="sav",
        amount=Decimal("50"),
        type=TransactionType.TRANSFER,
        transfer_id="existing",
    )

    assert find_transfer_matches([out, inn], ACCOUNTS) == []


def test_find_transfer_matches_does_not_pair_a_row_with_itself():
    # counterparty_account happens to resolve to the row's own account.
    solo = _txn(
        id="a", account_id="chk", amount=Decimal("-50"), counterparty_account="111222"
    )

    assert find_transfer_matches([solo], ACCOUNTS) == []


def test_find_transfer_matches_matches_each_row_at_most_once():
    out = _txn(
        id="a", account_id="chk", amount=Decimal("-50"), counterparty_account="333444"
    )
    inn1 = _txn(id="b", account_id="sav", amount=Decimal("50"))
    inn2 = _txn(id="c", account_id="sav", amount=Decimal("50"))

    matches = find_transfer_matches([out, inn1, inn2], ACCOUNTS)

    assert len(matches) == 1
    assert matches[0].to_transaction_id in {"b", "c"}


def test_merge_into_transfer_links_both_rows_unchanged_otherwise():
    out = _txn(
        id="a",
        account_id="chk",
        date=date(2026, 1, 5),
        amount=Decimal("-50"),
        description="to savings",
    )
    inn = _txn(
        id="b",
        account_id="sav",
        date=date(2026, 1, 6),
        amount=Decimal("50"),
        description="from checking",
    )
    match = TransferMatch(
        from_transaction_id="a",
        to_transaction_id="b",
        date=date(2026, 1, 5),
        from_account_id="chk",
        to_account_id="sav",
        amount=Decimal("50"),
        from_description="to savings",
        to_description="from checking",
    )

    [updated_out, updated_in] = merge_into_transfer([out, inn], match)

    assert updated_out.id == "a"
    assert updated_out.type is TransactionType.TRANSFER
    assert updated_out.category == "Transfer"
    assert updated_out.transfer_id == updated_in.transfer_id
    assert updated_out.amount == Decimal("-50")
    assert updated_out.description == "to savings"
    assert updated_in.id == "b"
    assert updated_in.type is TransactionType.TRANSFER
    assert updated_in.amount == Decimal("50")


def test_merge_into_transfer_raises_for_missing_transaction():
    out = _txn(id="a", account_id="chk", amount=Decimal("-50"))
    match = TransferMatch(
        from_transaction_id="a",
        to_transaction_id="ghost",
        date=date(2026, 1, 5),
        from_account_id="chk",
        to_account_id="sav",
        amount=Decimal("50"),
        from_description="",
        to_description="",
    )

    with pytest.raises(ValueError):
        merge_into_transfer([out], match)


def test_merge_into_transfer_raises_if_already_a_transfer():
    out = _txn(
        id="a",
        account_id="chk",
        amount=Decimal("-50"),
        type=TransactionType.TRANSFER,
        transfer_id="x",
    )
    inn = _txn(
        id="b",
        account_id="sav",
        amount=Decimal("50"),
        type=TransactionType.TRANSFER,
        transfer_id="x",
    )
    match = TransferMatch(
        from_transaction_id="a",
        to_transaction_id="b",
        date=date(2026, 1, 5),
        from_account_id="chk",
        to_account_id="sav",
        amount=Decimal("50"),
        from_description="",
        to_description="",
    )

    with pytest.raises(ValueError):
        merge_into_transfer([out, inn], match)


def test_apply_transfer_matches_merges_every_match():
    out = _txn(id="a", account_id="chk", amount=Decimal("-50"))
    inn = _txn(id="b", account_id="sav", amount=Decimal("50"))
    match = TransferMatch(
        from_transaction_id="a",
        to_transaction_id="b",
        date=date(2026, 1, 5),
        from_account_id="chk",
        to_account_id="sav",
        amount=Decimal("50"),
        from_description="",
        to_description="",
    )

    result = apply_transfer_matches([out, inn], [match])

    assert all(t.type is TransactionType.TRANSFER for t in result)


def test_apply_transfer_matches_skips_stale_match():
    out = _txn(id="a", account_id="chk", amount=Decimal("-50"))
    match = TransferMatch(
        from_transaction_id="a",
        to_transaction_id="ghost",
        date=date(2026, 1, 5),
        from_account_id="chk",
        to_account_id="sav",
        amount=Decimal("50"),
        from_description="",
        to_description="",
    )

    result = apply_transfer_matches([out], [match])

    assert result == [out]


def test_find_orphan_transfer_candidates_finds_an_unpaired_registered_counterparty():
    out = _txn(
        id="a",
        account_id="chk",
        amount=Decimal("-50"),
        counterparty_account="333444",
        description="Own transfer",
    )
    # sav has no transactions at all, so this can never match via
    # find_transfer_matches.

    [candidate] = find_orphan_transfer_candidates([out], ACCOUNTS)

    assert candidate.transaction_id == "a"
    assert candidate.account_id == "chk"
    assert candidate.counterparty_account_id == "sav"
    assert candidate.amount == Decimal("-50")
    assert candidate.description == "Own transfer"


def test_find_orphan_transfer_candidates_excludes_rows_already_matched():
    out = _txn(
        id="a", account_id="chk", amount=Decimal("-50"), counterparty_account="333444"
    )
    inn = _txn(id="b", account_id="sav", amount=Decimal("50"))

    assert find_orphan_transfer_candidates([out, inn], ACCOUNTS) == []


def test_find_orphan_transfer_candidates_ignores_unregistered_counterparty():
    out = _txn(
        id="a", account_id="chk", amount=Decimal("-50"), counterparty_account="999999"
    )

    assert find_orphan_transfer_candidates([out], ACCOUNTS) == []


def test_find_orphan_transfer_candidates_sorted_by_date():
    late = _txn(
        id="a",
        account_id="chk",
        date=date(2026, 3, 1),
        amount=Decimal("-10"),
        counterparty_account="333444",
    )
    early = _txn(
        id="b",
        account_id="chk",
        date=date(2026, 1, 1),
        amount=Decimal("-10"),
        counterparty_account="333444",
    )

    candidates = find_orphan_transfer_candidates([late, early], ACCOUNTS)

    assert [c.transaction_id for c in candidates] == ["b", "a"]


def test_synthesize_and_merge_transfer_creates_the_missing_leg():
    out = _txn(
        id="a",
        account_id="chk",
        date=date(2026, 3, 4),
        amount=Decimal("-50"),
        description="Own transfer",
    )
    candidate = OrphanTransferCandidate(
        transaction_id="a",
        date=date(2026, 3, 4),
        account_id="chk",
        counterparty_account_id="sav",
        amount=Decimal("-50"),
        description="Own transfer",
    )

    result = synthesize_and_merge_transfer([out], candidate)

    assert len(result) == 2
    updated_out = next(t for t in result if t.id == "a")
    new_leg = next(t for t in result if t.id != "a")
    assert updated_out.type is TransactionType.TRANSFER
    assert updated_out.amount == Decimal("-50")
    assert new_leg.type is TransactionType.TRANSFER
    assert new_leg.account_id == "sav"
    assert new_leg.amount == Decimal("50")
    assert new_leg.date == date(2026, 3, 4)
    assert new_leg.description == "Own transfer"
    assert new_leg.transfer_id == updated_out.transfer_id


def test_synthesize_and_merge_transfer_handles_an_inflow_anchor():
    """The anchor row can also be an inflow — the synthesized leg then outflows."""
    inn = _txn(id="a", account_id="chk", amount=Decimal("50"))
    candidate = OrphanTransferCandidate(
        transaction_id="a",
        date=date(2026, 1, 1),
        account_id="chk",
        counterparty_account_id="sav",
        amount=Decimal("50"),
        description="",
    )

    result = synthesize_and_merge_transfer([inn], candidate)

    new_leg = next(t for t in result if t.id != "a")
    assert new_leg.account_id == "sav"
    assert new_leg.amount == Decimal("-50")
    assert new_leg.type is TransactionType.TRANSFER


def test_synthesize_and_merge_transfer_rejects_a_stale_candidate():
    candidate = OrphanTransferCandidate(
        transaction_id="ghost",
        date=date(2026, 1, 1),
        account_id="chk",
        counterparty_account_id="sav",
        amount=Decimal("-50"),
        description="",
    )

    with pytest.raises(ValueError):
        synthesize_and_merge_transfer([], candidate)


def test_synthesize_and_merge_transfer_rejects_an_already_transferred_row():
    already = _txn(
        id="a", account_id="chk", type=TransactionType.TRANSFER, transfer_id="x"
    )
    candidate = OrphanTransferCandidate(
        transaction_id="a",
        date=date(2026, 1, 1),
        account_id="chk",
        counterparty_account_id="sav",
        amount=Decimal("-50"),
        description="",
    )

    with pytest.raises(ValueError):
        synthesize_and_merge_transfer([already], candidate)
