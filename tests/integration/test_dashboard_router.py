"""Integration tests for the dashboard router."""

from datetime import date
from decimal import Decimal


def _create_account(
    client, account_id="chk", starting_balance="1000.00", status="active"
):
    client.post(
        "/accounts",
        data={
            "account_id": account_id,
            "name": account_id.title(),
            "number": "1",
            "description": "",
            "starting_balance": starting_balance,
            "status": status,
        },
    )


def _create_transaction(
    client, *, account_id="chk", date_str, type, category, subcategory="", amount
):
    client.post(
        "/transactions",
        data={
            "account_id": account_id,
            "date": date_str,
            "type": type,
            "category": category,
            "subcategory": subcategory,
            "description": "",
            "amount": amount,
            "notes": "",
        },
    )


def test_root_redirects_to_dashboard(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/dashboard"


def test_dashboard_empty_state(client):
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "0.00" in response.text
    assert "No active accounts." in response.text
    assert "No transactions yet." in response.text
    assert "Nothing needs attention." in response.text


def test_dashboard_shows_this_month_income_and_expense(client):
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client,
        date_str=today,
        type="income",
        category="Salary",
        amount="2000.00",
    )
    _create_transaction(
        client,
        date_str=today,
        type="expense",
        category="Groceries",
        amount="150.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "2000.00" in response.text
    assert "150.00" in response.text
    assert "1850.00" in response.text  # net = 2000 - 150


def test_dashboard_shows_active_account_balance(client):
    _create_account(client, "chk", starting_balance="500.00")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Chk" in response.text
    assert "500.00" in response.text


def test_dashboard_excludes_closed_accounts_with_no_mention_of_them(client):
    _create_account(client, "chk", status="active")
    _create_account(client, "old", starting_balance="0.00", status="closed")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Chk" in response.text
    assert "closed account" not in response.text.lower()


def test_dashboard_flags_uncategorized_transactions(client):
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Uncategorized",
        amount="40.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "1 uncategorized transaction" in response.text


def test_dashboard_flags_over_budget_category(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="150.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "utilization-over" in response.text
    assert "150% of budget" in response.text


def test_dashboard_budget_status_shows_categories_at_60_pct_and_explains_it(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="65.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "65% of budget" in response.text
    assert "60%+" in response.text


def test_dashboard_budget_status_excludes_categories_under_60_pct(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="50.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "No budgeted category is over 60% this month." in response.text
    assert "utilization-ring" not in response.text


def test_dashboard_shows_recent_transactions_newest_first(client):
    _create_account(client)
    _create_transaction(
        client,
        date_str="2026-01-01",
        type="expense",
        category="Groceries",
        amount="10.00",
    )
    _create_transaction(
        client,
        date_str="2026-01-15",
        type="expense",
        category="Groceries",
        amount="20.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.text.index("Thu, Jan 15") < response.text.index("Thu, Jan 01")


def test_dashboard_groups_same_day_transactions_under_one_day_header(client):
    _create_account(client)
    _create_transaction(
        client,
        date_str="2026-01-15",
        type="expense",
        category="Groceries",
        amount="10.00",
    )
    _create_transaction(
        client,
        date_str="2026-01-15",
        type="expense",
        category="Groceries",
        amount="20.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.text.count("Thu, Jan 15") == 1
    assert "10.00" in response.text
    assert "20.00" in response.text


def test_dashboard_recent_days_caps_at_5_distinct_dates_with_activity(client):
    _create_account(client)
    for day in range(1, 8):
        _create_transaction(
            client,
            date_str=f"2026-01-{day:02d}",
            type="expense",
            category="Groceries",
            amount="10.00",
        )

    response = client.get("/dashboard")

    assert response.status_code == 200
    # The 7 most recent distinct dates are Jan 01-07; only the newest 5
    # (Jan 03-07) should show.
    assert "Jan 07" in response.text
    assert "Jan 03" in response.text
    assert "Jan 02" not in response.text
    assert "Jan 01" not in response.text


def _months_ago(today, n):
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 15)


def test_dashboard_top_categories_shows_vs_last_month_and_12mo_avg(client):
    _create_account(client)
    today = date.today()

    other_months_amount = Decimal("100.00")
    for n in range(2, 13):
        _create_transaction(
            client,
            date_str=_months_ago(today, n).isoformat(),
            type="expense",
            category="Groceries",
            amount=str(other_months_amount),
        )
    last_month_amount = Decimal("200.00")
    _create_transaction(
        client,
        date_str=_months_ago(today, 1).isoformat(),
        type="expense",
        category="Groceries",
        amount=str(last_month_amount),
    )
    this_month_amount = Decimal("150.00")
    _create_transaction(
        client,
        date_str=today.isoformat(),
        type="expense",
        category="Groceries",
        amount=str(this_month_amount),
    )

    avg_12mo = (other_months_amount * 11 + last_month_amount) / 12
    vs_last_month_pct = (
        (this_month_amount - last_month_amount) / last_month_amount * 100
    )
    vs_avg_pct = (this_month_amount - avg_12mo) / avg_12mo * 100

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert f"{abs(vs_last_month_pct):.0f}% vs last month" in response.text
    assert f"{abs(vs_avg_pct):.0f}% vs 12mo avg" in response.text
    # Spent less than last month (-25%) -> "up"/green; spent more than
    # the 12-month average (+38%) -> "down"/red.
    assert "dash-cat-bar-delta up" in response.text
    assert "dash-cat-bar-delta down" in response.text


def test_dashboard_top_categories_omits_comparisons_for_brand_new_category(client):
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="50.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Groceries" in response.text
    assert "vs last month" not in response.text
    assert "vs 12mo avg" not in response.text
