"""Reset local data/config to fake sample data for manual browser testing.

Overwrites `config/accounts.toml`, `config/categories.toml`, and
`data/ledger.csv` with a small set of made-up accounts, categories, and
~50 transactions spanning five months (including several transfer pairs).
Safe to rerun at any time — each run fully overwrites rather than
appending, so it doubles as a "reset to a known state" tool. Never touches
real user data by design: this script only ever writes fake values it
generates itself.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.account import Account  # noqa: E402
from app.models.category import CategoriesByType  # noqa: E402
from app.models.transaction import Transaction, TransactionType  # noqa: E402
from app.services.transactions import new_transaction, new_transfer_pair  # noqa: E402
from app.storage.accounts import write_accounts  # noqa: E402
from app.storage.categories import write_categories  # noqa: E402
from app.storage.ledger import write_ledger  # noqa: E402


def build_sample_accounts() -> list[Account]:
    """Return a small set of fake accounts."""
    return [
        Account(
            id="chk",
            name="Everyday Checking",
            number="1234",
            description="Main checking account",
            starting_balance=Decimal("2500.00"),
        ),
        Account(
            id="sav",
            name="Emergency Savings",
            number="5678",
            description="Rainy-day fund",
            starting_balance=Decimal("8000.00"),
        ),
        Account(
            id="cc",
            name="Rewards Credit Card",
            number="9012",
            description="Primary credit card",
            starting_balance=Decimal("-320.15"),
        ),
    ]


def build_sample_categories() -> CategoriesByType:
    """Return small fake income and expense category trees."""
    return {
        "income": {
            "Salary": [],
            "Freelance": [],
        },
        "expense": {
            "Groceries": ["Supermarket", "Farmers Market"],
            "Housing": ["Rent", "Utilities"],
            "Transportation": ["Fuel", "Public Transit"],
            "Entertainment": ["Dining Out", "Streaming"],
            "Shopping": ["Clothing", "Electronics"],
            "Health": ["Pharmacy"],
        },
    }


def _expense(
    account_ids: list[str],
    *,
    account_id: str,
    day: tuple[int, int, int],
    category: str,
    subcategory: str,
    description: str,
    amount: str,
) -> Transaction:
    """Shorthand for building one expense transaction."""
    return new_transaction(
        account_ids,
        account_id=account_id,
        date=date(*day),
        category=category,
        subcategory=subcategory,
        description=description,
        amount=Decimal(amount),
        type=TransactionType.EXPENSE,
    )


def _income(
    account_ids: list[str],
    *,
    day: tuple[int, int, int],
    category: str,
    description: str,
    amount: str,
) -> Transaction:
    """Shorthand for building one income transaction (always into checking)."""
    return new_transaction(
        account_ids,
        account_id="chk",
        date=date(*day),
        category=category,
        subcategory="",
        description=description,
        amount=Decimal(amount),
        type=TransactionType.INCOME,
    )


def _typical_month(
    account_ids: list[str],
    *,
    year: int,
    month: int,
    groceries: list[tuple[int, str]],
    extra: list[Transaction] | None = None,
) -> list[Transaction]:
    """Build one month's worth of typical recurring transactions.

    ``groceries`` is a list of ``(day, amount)`` pairs so each month's
    grocery runs can vary without a whole separate transaction list per
    month. ``extra`` appends one-off transactions specific to that month
    (a freelance payment, a bigger purchase, ...). Every month gets the
    same recurring paycheck/rent/utilities/transfer pattern with slightly
    different amounts, since real bills vary a little month to month.
    """
    txns = [
        _income(
            account_ids,
            day=(year, month, 1),
            category="Salary",
            description="Monthly paycheck",
            amount="3200.00",
        ),
        _expense(
            account_ids,
            account_id="chk",
            day=(year, month, 3),
            category="Housing",
            subcategory="Rent",
            description="Monthly rent",
            amount="1500.00",
        ),
        _expense(
            account_ids,
            account_id="chk",
            day=(year, month, 16),
            category="Housing",
            subcategory="Utilities",
            description="Electric & water",
            amount=f"{95 + month * 3}.40",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(year, month, 9),
            category="Transportation",
            subcategory="Fuel",
            description="Gas station",
            amount=f"{44 + month}.50",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(year, month, 20),
            category="Entertainment",
            subcategory="Streaming",
            description="Streaming subscription",
            amount="15.99",
        ),
    ]
    for day, amount in groceries:
        txns.append(
            _expense(
                account_ids,
                account_id="cc",
                day=(year, month, day),
                category="Groceries",
                subcategory="Supermarket",
                description="Grocery run",
                amount=amount,
            )
        )
    txns.extend(extra or [])

    transfer_out, transfer_in = new_transfer_pair(
        account_ids,
        from_account_id="chk",
        to_account_id="sav",
        date=date(year, month, 2),
        amount=Decimal("500.00"),
        description="Monthly savings transfer",
    )
    txns.extend([transfer_out, transfer_in])
    return txns


def build_sample_transactions(account_ids: list[str]) -> list[Transaction]:
    """Return ~50 fake transactions spanning May-September 2026."""
    transactions: list[Transaction] = []

    transactions += _typical_month(
        account_ids,
        year=2026,
        month=5,
        groceries=[(5, "84.20"), (12, "91.15"), (19, "76.40")],
        extra=[
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 5, 23),
                category="Groceries",
                subcategory="Farmers Market",
                description="Farmers market",
                amount="28.75",
            ),
        ],
    )

    transactions += _typical_month(
        account_ids,
        year=2026,
        month=6,
        groceries=[(6, "88.60"), (14, "42.00"), (21, "79.30")],
        extra=[
            _income(
                account_ids,
                day=(2026, 6, 18),
                category="Freelance",
                description="Contract invoice",
                amount="450.00",
            ),
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 6, 11),
                category="Entertainment",
                subcategory="Dining Out",
                description="Dinner with friends",
                amount="64.20",
            ),
        ],
    )

    transactions += _typical_month(
        account_ids,
        year=2026,
        month=7,
        groceries=[(10, "112.34"), (24, "67.90")],
        extra=[
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 7, 22),
                category="Entertainment",
                subcategory="Dining Out",
                description="Dinner out",
                amount="48.00",
            ),
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 7, 27),
                category="Shopping",
                subcategory="Clothing",
                description="New jacket",
                amount="89.99",
            ),
            _expense(
                account_ids,
                account_id="chk",
                day=(2026, 7, 29),
                category="Transportation",
                subcategory="Public Transit",
                description="Monthly transit pass",
                amount="75.00",
            ),
        ],
    )

    transactions += _typical_month(
        account_ids,
        year=2026,
        month=8,
        groceries=[(9, "98.50"), (23, "71.25")],
        extra=[
            _income(
                account_ids,
                day=(2026, 8, 18),
                category="Freelance",
                description="Contract invoice",
                amount="450.00",
            ),
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 8, 5),
                category="Entertainment",
                subcategory="Dining Out",
                description="Dinner with friends",
                amount="64.20",
            ),
            _expense(
                account_ids,
                account_id="cc",
                day=(2026, 8, 14),
                category="Shopping",
                subcategory="Electronics",
                description="Replacement headphones",
                amount="59.00",
            ),
        ],
    )

    # September is partial (today is the 12th), so it's built directly
    # rather than via _typical_month, which assumes a full month of bills.
    transactions += [
        _income(
            account_ids,
            day=(2026, 9, 1),
            category="Salary",
            description="Monthly paycheck",
            amount="3200.00",
        ),
        _expense(
            account_ids,
            account_id="chk",
            day=(2026, 9, 3),
            category="Housing",
            subcategory="Rent",
            description="Monthly rent",
            amount="1500.00",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(2026, 9, 4),
            category="Groceries",
            subcategory="Supermarket",
            description="Grocery run",
            amount="76.20",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(2026, 9, 5),
            category="Health",
            subcategory="Pharmacy",
            description="Prescription refill",
            amount="22.50",
        ),
        _expense(
            account_ids,
            account_id="chk",
            day=(2026, 9, 6),
            category="Housing",
            subcategory="Utilities",
            description="Electric & water",
            amount="145.00",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(2026, 9, 8),
            category="Transportation",
            subcategory="Fuel",
            description="Gas station",
            amount="41.00",
        ),
        _expense(
            account_ids,
            account_id="cc",
            day=(2026, 9, 10),
            category="Entertainment",
            subcategory="Streaming",
            description="Streaming subscription",
            amount="15.99",
        ),
    ]
    transfer_out, transfer_in = new_transfer_pair(
        account_ids,
        from_account_id="chk",
        to_account_id="sav",
        date=date(2026, 9, 2),
        amount=Decimal("500.00"),
        description="Monthly savings transfer",
    )
    transactions.extend([transfer_out, transfer_in])

    return transactions


def main() -> None:
    """Overwrite the local data/config files with fake sample data."""
    accounts = build_sample_accounts()
    categories = build_sample_categories()
    transactions = build_sample_transactions([account.id for account in accounts])

    write_accounts(accounts)
    write_categories(categories)
    write_ledger(transactions)

    category_count = sum(len(tree) for tree in categories.values())
    print(
        f"Seeded {len(accounts)} accounts, {category_count} categories "
        f"(income + expense), {len(transactions)} ledger rows."
    )


if __name__ == "__main__":
    main()
