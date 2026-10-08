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


def test_rolling_windows_have_tabs_and_panels(client):
    _seed()

    text = client.get("/reports/budget-rule?month=2026-03").text

    for label in ("Last 3 mo", "Last 6 mo", "Last 12 mo"):
        assert label in text
    assert "Last 3 months to March 2026" in text
    assert 'data-panel="r12"' in text


def test_rolling_window_excludes_older_months(client):
    _seed()  # all activity in March 2026

    text = client.get("/reports/budget-rule?month=2026-07").text

    # March is outside a 3-month window ending July, inside a 6-month one.
    panel3 = text.split('data-panel="r3"')[1].split("</section>")[0]
    panel6 = text.split('data-panel="r6"')[1].split("</section>")[0]
    assert "No income in this period" in panel3
    assert "No income in this period" not in panel6


def test_month_navigation_controls(client):
    _seed()

    past = client.get("/reports/budget-rule?month=2026-03").text
    assert 'name="month"' in past and 'value="2026-03"' in past
    assert "Back to current month" in past
    assert "Previous: February 2026" in past
    assert "Next: April 2026" in past

    current = client.get("/reports/budget-rule").text
    assert "Back to current month" not in current
    assert 'class="step disabled"' in current


def test_unclassified_row_hidden_when_zero(client):
    _seed()  # Misc spend is unclassified in March only

    def month_panel(month):
        text = client.get(f"/reports/budget-rule?month={month}").text
        return text.split('data-panel="month"')[1].split("</section>")[0]

    assert "br-swatch-unclassified" in month_panel("2026-03")
    assert "br-swatch-unclassified" not in month_panel("2026-05")


def test_page_has_non_sticky_section_index(client):
    text = client.get("/reports/budget-rule").text

    assert 'class="section-index static"' in text
    assert 'id="breakdown"' in text and 'id="trend"' in text
