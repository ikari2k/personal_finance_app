"""Integration tests for the reports router."""


def _create_account(client, account_id="chk", starting_balance="1000.00"):
    client.post(
        "/accounts",
        data={
            "account_id": account_id,
            "name": account_id.title(),
            "number": "1",
            "description": "",
            "starting_balance": starting_balance,
        },
    )


def _create_transaction(
    client, *, account_id="chk", date, type, category, subcategory="", amount
):
    client.post(
        "/transactions",
        data={
            "account_id": account_id,
            "date": date,
            "type": type,
            "category": category,
            "subcategory": subcategory,
            "description": "",
            "amount": amount,
            "notes": "",
        },
    )


def test_reports_overview_with_no_transactions(client):
    response = client.get("/reports")

    assert response.status_code == 200
    assert "No transactions yet" in response.text


def test_reports_overview_lists_years_and_links_to_detail(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-03-01", type="income", category="Salary", amount="1000"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    assert "2026" in response.text
    assert 'href="/reports/2026"' in response.text


def test_reports_overview_renders_net_worth_chart(client):
    _create_account(client, starting_balance="500.00")
    _create_transaction(
        client, date="2026-03-01", type="income", category="Salary", amount="200"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    assert "net-worth-chart" in response.text
    assert "700.00" in response.text


def test_reports_overview_chart_renders_fixed_zero_based_y_axis(client):
    _create_account(client, starting_balance="500.00")
    _create_transaction(
        client, date="2026-01-01", type="income", category="Salary", amount="200"
    )
    _create_transaction(
        client, date="2026-02-01", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    net_worth_svg = response.text.split('aria-label="Net worth over time"')[1].split(
        "</svg>"
    )[0]
    # Net worth stays within 0-5000 (700 then 650), so the fixed
    # every-5000-starting-from-0 axis has exactly two ticks: 0 and 5,000.
    assert net_worth_svg.count("chart-gridline") == 2
    assert net_worth_svg.count("chart-axis-label") == 2
    assert ">0<" in net_worth_svg
    assert ">5,000<" in net_worth_svg


def test_reports_overview_chart_includes_monthly_income_expense_bars(client):
    _create_account(client, starting_balance="0.00")
    _create_transaction(
        client, date="2026-01-05", type="income", category="Salary", amount="1000"
    )
    _create_transaction(
        client,
        date="2026-01-20",
        type="expense",
        category="Groceries",
        amount="200",
    )
    _create_transaction(
        client, date="2026-02-05", type="income", category="Salary", amount="1200"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    net_worth_svg = response.text.split('aria-label="Net worth over time"')[1].split(
        "</svg>"
    )[0]
    # One income + one expense rect per month (a month with no expense
    # still gets a zero-height rect, not an omitted one).
    assert net_worth_svg.count("bar-income") == 2
    assert net_worth_svg.count("bar-expense") == 2
    assert "January 2026 income: 1,000.00" in net_worth_svg
    assert "January 2026 expense: 200.00" in net_worth_svg
    assert "chart-legend" in response.text


def test_year_detail_with_no_transactions(client):
    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "No transactions in 2026" in response.text
    assert "No data" in response.text


def test_year_detail_shows_monthly_breakdown_and_category_totals(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="income", category="Salary", amount="1000"
    )
    _create_transaction(
        client,
        date="2026-01-20",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "January 2026" in response.text
    assert "Salary" in response.text
    assert "Groceries" in response.text
    assert "Supermarket" in response.text


def test_year_detail_shows_category_icon_when_one_is_set(client):
    client.post("/categories/expense", data={"name": "Groceries", "icon": "cart"})
    _create_account(client)
    _create_transaction(
        client, date="2026-01-20", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "cat-cell-icon-expense" in response.text


def test_year_detail_omits_icon_span_for_a_category_with_no_icon(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-20", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "cat-cell-icon" not in response.text


def test_year_detail_shows_month_to_month_category_matrix(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="50"
    )
    _create_transaction(
        client, date="2026-02-15", type="expense", category="Groceries", amount="80"
    )
    _create_transaction(
        client, date="2026-01-15", type="income", category="Salary", amount="1000"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "Expense by category — month to month" in response.text
    assert "Income by category — month to month" in response.text
    # Jan/Feb column headers (abbreviated) plus each month's own amount,
    # including the zero-filled month with no Groceries activity.
    assert ">Jan<" in response.text
    assert ">Feb<" in response.text
    assert ">50.00<" in response.text
    assert ">80.00<" in response.text


def test_year_detail_shows_utilization_ring_for_a_budgeted_category(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "utilization-ring" in response.text
    assert "50% of budget" in response.text


def test_year_detail_over_budget_month_flags_utilization_over(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "50.00"})
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="150"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "utilization-over" in response.text
    assert "300% of budget" in response.text


def test_year_detail_omits_ring_for_an_unbudgeted_category(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "utilization-ring" not in response.text


def test_year_detail_shows_subcategory_row_and_its_own_utilization_ring(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "500.00"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "budget": "80.00"},
    )
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-15",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="40",
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "Supermarket" in response.text
    assert "subcategory-row" in response.text
    assert "50% of budget" in response.text


def test_year_detail_only_includes_that_years_months(client):
    _create_account(client)
    _create_transaction(
        client, date="2025-06-01", type="income", category="Salary", amount="1000"
    )
    _create_transaction(
        client, date="2026-01-01", type="income", category="Salary", amount="1000"
    )

    response = client.get("/reports/2026")

    assert "January 2026" in response.text
    assert "June 2025" not in response.text
