"""Integration tests for the 50/30/20 report router."""

from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.models.transaction import Transaction, TransactionType
from app.storage.accounts import write_accounts
from app.storage.budget_rule import read_targets
from app.storage.categories import write_categories
from app.storage.ledger import write_ledger


def _seed():
    write_accounts(
        [
            Account(id="chk", name="Chk", number="1", starting_balance=Decimal("0")),
            Account(
                id="sav",
                name="Sav",
                number="2",
                starting_balance=Decimal("0"),
                account_type=AccountType.SAVINGS,
            ),
        ]
    )
    write_categories(
        {
            "income": {
                "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
            },
            "expense": {
                "Rent": {
                    "icon": "",
                    "budget": "",
                    "bucket": "need",
                    "subcategories": {},
                },
                "Misc": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
            },
        }
    )

    def t(id, amount, type_, category="", account="chk"):
        return Transaction(
            id=id,
            date=date(2026, 3, 5),
            account_id=account,
            category=category,
            subcategory="",
            description="d",
            amount=Decimal(amount),
            type=type_,
        )

    write_ledger(
        [
            t("a", "1000", TransactionType.INCOME, "Salary"),
            t("b", "-500", TransactionType.EXPENSE, "Rent"),
            t("c", "-100", TransactionType.EXPENSE, "Misc"),
            t("d", "200", TransactionType.TRANSFER, account="sav"),
            t("e", "-200", TransactionType.TRANSFER),
        ]
    )


def test_page_renders_with_empty_data(client):
    response = client.get("/reports/budget-rule")

    assert response.status_code == 200
    assert "50/30/20" in response.text


def test_page_shows_split_for_month(client):
    _seed()

    response = client.get("/reports/budget-rule?month=2026-03")

    assert response.status_code == 200
    assert "March 2026" in response.text
    assert "50.0%" in response.text  # needs
    assert "20.0%" in response.text  # savings
    assert "classify these" in response.text  # Misc is unclassified


def test_invalid_month_is_404(client):
    assert client.get("/reports/budget-rule?month=nope").status_code == 404
    assert client.get("/reports/budget-rule?month=2026-13").status_code == 404


def test_edit_form_prefilled_with_defaults(client):
    response = client.get("/reports/budget-rule/targets/edit")

    assert 'name="needs"' in response.text and 'value="50"' in response.text


def test_save_targets_persists_and_closes_dialog(client):
    _seed()

    response = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "60", "wants": "20", "savings": "20", "month": "2026-03"},
    )

    assert response.status_code == 200
    assert read_targets().needs == 60
    assert "close-dialog" in response.headers["HX-Trigger"]
    assert 'hx-swap-oob="true"' in response.text


def test_save_targets_rejects_bad_sum_and_non_numbers(client):
    bad_sum = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "60", "wants": "30", "savings": "30"},
    )
    assert "sum to 100" in bad_sum.text
    assert "HX-Trigger" not in bad_sum.headers

    not_numbers = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "a", "wants": "30", "savings": "20"},
    )
    assert "whole numbers" in not_numbers.text
    assert read_targets().needs == 50
