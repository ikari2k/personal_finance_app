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


def test_dashboard_budget_status_shows_categories_at_75_pct_and_explains_it(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="80.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "80% of budget" in response.text
    assert "75%+" in response.text


def test_dashboard_budget_status_excludes_categories_under_75_pct(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client,
        date_str=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="70.00",
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "No budgeted category is over 75% this month." in response.text
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


def test_dashboard_top_categories_shows_vs_last_month_and_all_time_avg(client):
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

    avg_all_time = (other_months_amount * 11 + last_month_amount) / 12
    vs_last_month_pct = (
        (this_month_amount - last_month_amount) / last_month_amount * 100
    )
    vs_avg_pct = (this_month_amount - avg_all_time) / avg_all_time * 100

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert f"{abs(vs_last_month_pct):.0f}%" in response.text
    assert f"{abs(vs_avg_pct):.0f}%" in response.text
    # Spent less than last month (-25%) -> "up"/green; spent more than
    # the all-time monthly average (+38%) -> "down"/red.
    assert "dash-cat-line-delta up" in response.text
    assert "dash-cat-line-delta down" in response.text


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
    assert "dash-cat-line-delta up" not in response.text
    assert "dash-cat-line-delta down" not in response.text


def _seed_forecastable_history(client):
    """Four prior months of a 200 expense plus one today, so a forecast exists."""
    from datetime import date as _date

    _create_account(client)
    today = _date.today()
    index = today.year * 12 + today.month - 1
    for back in (1, 2, 3, 4):
        year, month0 = divmod(index - back, 12)
        _create_transaction(
            client,
            date_str=_date(year, month0 + 1, 28).isoformat(),
            type="expense",
            category="Groceries",
            amount="200",
        )
    _create_transaction(
        client,
        date_str=today.isoformat(),
        type="expense",
        category="Groceries",
        amount="50",
    )


def test_dashboard_shows_month_end_projection(client):
    _seed_forecastable_history(client)

    response = client.get("/dashboard")

    assert "On pace for ~" in response.text


def test_dashboard_budget_status_splits_over_budget_from_nearing_limit(client):
    _create_account(client)
    for name, spent in [("Over", "150.00"), ("Near", "90.00"), ("Fine", "10.00")]:
        client.post("/categories/expense", data={"name": name, "budget": "100.00"})
        _create_transaction(
            client,
            date_str=date.today().isoformat(),
            type="expense",
            category=name,
            amount=spent,
        )

    text = client.get("/dashboard").text

    assert "Over budget" in text
    assert "Nearing limit" in text
    assert text.index("Over budget") < text.index("Nearing limit")
    assert "Over: 150% of budget" in text
    assert "Near: 90% of budget" in text
    assert "Fine:" not in text


def test_dashboard_budget_status_shows_every_qualifying_category(client):
    _create_account(client)
    for i in range(8):
        name = f"Cat{i}"
        client.post("/categories/expense", data={"name": name, "budget": "100.00"})
        _create_transaction(
            client,
            date_str=date.today().isoformat(),
            type="expense",
            category=name,
            amount="120.00",
        )

    text = client.get("/dashboard").text

    assert all(f"Cat{i}: 120% of budget" in text for i in range(8))
    assert "Nearing limit" not in text


def test_dashboard_shows_budget_rule_widget(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "50/30/20" in response.text
    assert "No income recorded this month yet" in response.text
