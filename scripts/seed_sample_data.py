"""Reset local data/config to fake sample data for manual browser testing.

Overwrites `config/accounts.toml`, `config/categories.toml`, and
`data/ledger.csv` with a small set of made-up accounts, categories, and
transactions (including one transfer pair). Safe to rerun at any time —
each run fully overwrites rather than appending, so it doubles as a
"reset to a known state" tool. Never touches real user data by design:
this script only ever writes fake values it generates itself.
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
        },
    }


def build_sample_transactions(account_ids: list[str]) -> list[Transaction]:
    """Return a few months of fake transactions, including one transfer."""
    transactions = [
        new_transaction(
            account_ids,
            account_id="chk",
            date=date(2026, 7, 1),
            category="Salary",
            subcategory="",
            description="July paycheck",
            amount=Decimal("3200.00"),
            type=TransactionType.INCOME,
        ),
        new_transaction(
            account_ids,
            account_id="chk",
            date=date(2026, 7, 3),
            category="Housing",
            subcategory="Rent",
            description="July rent",
            amount=Decimal("1500.00"),
            type=TransactionType.EXPENSE,
        ),
        new_transaction(
            account_ids,
            account_id="cc",
            date=date(2026, 7, 10),
            category="Groceries",
            subcategory="Supermarket",
            description="Weekly grocery run",
            amount=Decimal("112.34"),
            type=TransactionType.EXPENSE,
        ),
        new_transaction(
            account_ids,
            account_id="chk",
            date=date(2026, 8, 1),
            category="Salary",
            subcategory="",
            description="August paycheck",
            amount=Decimal("3200.00"),
            type=TransactionType.INCOME,
        ),
        new_transaction(
            account_ids,
            account_id="cc",
            date=date(2026, 8, 5),
            category="Entertainment",
            subcategory="Dining Out",
            description="Dinner with friends",
            amount=Decimal("64.20"),
            type=TransactionType.EXPENSE,
        ),
    ]
    transfer_out, transfer_in = new_transfer_pair(
        account_ids,
        from_account_id="chk",
        to_account_id="sav",
        date=date(2026, 8, 2),
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
        f"(income + expense), {len(transactions)} transactions."
    )


if __name__ == "__main__":
    main()
