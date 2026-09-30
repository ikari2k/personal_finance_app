"""Integration tests for the reports router."""

from calendar import monthrange
from datetime import date


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


def test_reports_overview_chart_y_axis_starts_at_zero_and_adapts_step(client):
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
    # Values stay within 0-700ish, so _nice_step picks 200 (not a flat
    # 5,000) to land on a readable ~5 gridlines: 0/200/400/600/800.
    assert net_worth_svg.count("chart-gridline") == 5
    assert net_worth_svg.count("chart-axis-label") == 5
    assert ">0<" in net_worth_svg
    assert ">200<" in net_worth_svg
    assert ">800<" in net_worth_svg


def test_reports_overview_chart_y_axis_stays_uncrowded_for_large_values(client):
    _create_account(client, starting_balance="0.00")
    _create_transaction(
        client, date="2026-01-01", type="income", category="Salary", amount="130000"
    )
    _create_transaction(
        client, date="2026-01-05", type="expense", category="Rent", amount="128000"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    net_worth_svg = response.text.split('aria-label="Net worth over time"')[1].split(
        "</svg>"
    )[0]
    # A flat every-5,000 step would crowd this axis with ~27 gridlines;
    # the adaptive step keeps it to a small, readable handful instead.
    assert net_worth_svg.count("chart-gridline") <= 6
    assert ">50,000<" in net_worth_svg


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


def test_reports_overview_chart_shows_month_and_year_x_axis_labels(client):
    _create_account(client, starting_balance="0.00")
    _create_transaction(
        client, date="2025-12-05", type="income", category="Salary", amount="100"
    )
    _create_transaction(
        client, date="2026-01-05", type="income", category="Salary", amount="100"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    net_worth_svg = response.text.split('aria-label="Net worth over time"')[1].split(
        "</svg>"
    )[0]
    assert ">Dec<" in net_worth_svg
    assert ">Jan<" in net_worth_svg
    # The year label only appears once per year change, not on every
    # month column.
    assert net_worth_svg.count("chart-x-label-year") == 2
    assert ">2025<" in net_worth_svg
    assert ">2026<" in net_worth_svg


def test_reports_overview_chart_scroll_wrapper_caps_at_visible_months(client):
    _create_account(client, starting_balance="0.00")
    # 18 months, spanning a year boundary (Jan 2025 - Jun 2026) so every
    # month/date is genuinely valid.
    for i in range(18):
        year, month = divmod(i, 12)
        _create_transaction(
            client,
            date=f"{2025 + year}-{month + 1:02d}-05",
            type="income",
            category="Salary",
            amount="100",
        )

    response = client.get("/reports")

    assert response.status_code == 200
    assert "net-worth-chart-scroll" in response.text
    # 18 months of data, but the wrapper's max-width should only ever
    # show VISIBLE_MONTHS (15) of them at once (84 + 84*15 = 1344px).
    assert "max-width: 1344px" in response.text


def test_year_detail_with_no_transactions(client):
    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "No transactions in 2026" in response.text
    assert "No data" in response.text


def test_year_detail_shows_net_worth_chart_for_just_that_year(client):
    _create_account(client, starting_balance="1000.00")
    _create_transaction(
        client, date="2025-06-01", type="income", category="Salary", amount="500"
    )
    _create_transaction(
        client, date="2026-01-15", type="income", category="Salary", amount="200"
    )
    _create_transaction(
        client, date="2026-02-15", type="income", category="Salary", amount="300"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    net_worth_svg = response.text.split('aria-label="Net worth over time"')[1].split(
        "</svg>"
    )[0]
    # Only 2026's two months show, even though 2025 has earlier activity
    # that the cumulative net worth still needs to account for.
    assert net_worth_svg.count("chart-point") == 2
    assert "January 2026 net worth: 1,700.00" in net_worth_svg
    assert "February 2026 net worth: 2,000.00" in net_worth_svg
    assert "2025" not in net_worth_svg


def test_year_detail_chart_stretches_to_fill_since_a_year_never_needs_scroll(client):
    _create_account(client, starting_balance="0.00")
    _create_transaction(
        client, date="2026-01-15", type="income", category="Salary", amount="100"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "net-worth-chart-fill" in response.text
    assert "max-width:" not in response.text


def test_reports_overview_chart_scrolls_once_past_visible_months(client):
    _create_account(client, starting_balance="0.00")
    for i in range(18):
        year, month = divmod(i, 12)
        _create_transaction(
            client,
            date=f"{2025 + year}-{month + 1:02d}-05",
            type="income",
            category="Salary",
            amount="100",
        )

    response = client.get("/reports")

    assert response.status_code == 200
    assert "net-worth-chart-fill" not in response.text
    assert "max-width: 1344px" in response.text


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


def test_year_detail_shows_transaction_count_per_category(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-05", type="expense", category="Groceries", amount="40"
    )
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="20"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "# Txns" in response.text
    assert ">2<" in response.text


def test_year_detail_breakdown_links_category_to_filtered_transactions(client):
    _create_account(client)
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
    assert (
        "/transactions?txn_type=expense&category=Groceries&subcategory=&"
        "date_from=2026-01-01&date_to=2026-12-31" in response.text
    )
    assert (
        "/transactions?txn_type=expense&category=Groceries&"
        "subcategory=Supermarket&date_from=2026-01-01&date_to=2026-12-31"
        in response.text
    )


def test_year_detail_month_matrix_links_to_that_specific_month(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-02-20", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert (
        "/transactions?txn_type=expense&category=Groceries&subcategory=&"
        "date_from=2026-02-01&date_to=2026-02-28" in response.text
    )


def test_year_detail_breakdown_links_preserve_account_filter(client):
    _create_account(client, "chk")
    _create_transaction(
        client, date="2026-01-20", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026?account_id=chk")

    assert response.status_code == 200
    assert "account_id=chk" in response.text


def test_month_detail_breakdown_links_to_that_month_only(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-09-20", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026/9")

    assert response.status_code == 200
    assert (
        "/transactions?txn_type=expense&category=Groceries&subcategory=&"
        "date_from=2026-09-01&date_to=2026-09-30" in response.text
    )


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
    assert "utilization-under" in response.text
    assert "50% of budget" in response.text


def test_year_detail_utilization_warning_tier_between_75_and_99_pct(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    _create_transaction(
        client, date="2026-01-15", type="expense", category="Groceries", amount="80"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "utilization-warning" in response.text
    assert "80% of budget" in response.text


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


def test_month_detail_shows_prev_and_next_month_links(client):
    response = client.get("/reports/2026/6")

    assert response.status_code == 200
    assert 'href="/reports/2026/5"' in response.text
    assert "May 2026" in response.text
    assert 'href="/reports/2026/7"' in response.text
    assert "July 2026" in response.text


def test_month_detail_nav_wraps_to_prior_year_in_january(client):
    response = client.get("/reports/2026/1")

    assert 'href="/reports/2025/12"' in response.text
    assert "December 2025" in response.text


def test_month_detail_nav_wraps_to_next_year_in_december(client):
    response = client.get("/reports/2026/12")

    assert 'href="/reports/2027/1"' in response.text
    assert "January 2027" in response.text


def test_month_detail_nav_preserves_account_filter(client):
    _create_account(client, "chk")

    response = client.get("/reports/2026/6?account_id=chk")

    assert 'href="/reports/2026/5?account_id=chk"' in response.text
    assert 'href="/reports/2026/7?account_id=chk"' in response.text


def test_month_detail_rejects_invalid_month(client):
    response = client.get("/reports/2026/13")

    assert response.status_code == 404


def test_category_detail_rejects_transfer_type(client):
    response = client.get(
        "/reports/category", params={"txn_type": "transfer", "category": "Transfer"}
    )

    assert response.status_code == 404


def test_category_detail_shows_no_data_message_for_unused_category(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": ""})

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "No transactions in this category yet" in response.text


def test_category_detail_shows_total_and_trend_chart(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-10", type="expense", category="Groceries", amount="50"
    )
    _create_transaction(
        client, date="2026-02-10", type="expense", category="Groceries", amount="30"
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "Groceries" in response.text
    assert "80.00" in response.text  # all-time total
    assert "cat-chart-svg" in response.text
    assert "Jan 2026" in response.text


def test_category_detail_shows_budget_ring_for_current_month(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "100.00"})
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client, date=today, type="expense", category="Groceries", amount="50"
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "utilization-under" in response.text
    assert "50% of budget" in response.text


def test_category_detail_omits_ring_when_no_budget_set(client):
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client, date=today, type="expense", category="Groceries", amount="50"
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "No budget set for this category" in response.text


def test_category_detail_shows_subcategory_breakdown(client):
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client,
        date=today,
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="80",
    )
    _create_transaction(
        client,
        date=today,
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="20",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "Supermarket" in response.text
    assert "Farmers Market" in response.text
    assert "80.0%" in response.text
    assert "20.0%" in response.text


def test_category_detail_by_year_table_marks_current_year_in_progress(client):
    _create_account(client)
    _create_transaction(
        client,
        date=date(date.today().year, 1, 15).isoformat(),
        type="expense",
        category="Groceries",
        amount="50",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "(in progress)" in response.text


def test_category_detail_account_filter_round_trips(client):
    _create_account(client, "chk")
    _create_account(client, "sav")
    _create_transaction(
        client,
        account_id="chk",
        date="2026-01-10",
        type="expense",
        category="Groceries",
        amount="50",
    )
    _create_transaction(
        client,
        account_id="sav",
        date="2026-01-10",
        type="expense",
        category="Groceries",
        amount="999",
    )

    response = client.get(
        "/reports/category",
        params={"txn_type": "expense", "category": "Groceries", "account_id": "chk"},
    )

    assert response.status_code == 200
    assert "50.00" in response.text
    assert "999.00" not in response.text
    assert "account_id=chk" in response.text


def test_year_detail_breakdown_table_links_to_category_detail(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": ""})
    _create_account(client)
    _create_transaction(
        client, date="2026-01-10", type="expense", category="Groceries", amount="50"
    )

    response = client.get("/reports/2026")

    assert response.status_code == 200
    assert "cat-trend-link" in response.text
    assert "/reports/category/2026?txn_type=expense&category=Groceries" in response.text


def test_category_detail_shows_subcategory_stack_chart_when_any_exist(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "by subcategory</h2>" in response.text
    assert "cat-subcat-chart-scroll" in response.text
    assert "pie-slice-0" in response.text
    assert "Supermarket" in response.text.split("by subcategory</h2>")[1][:500]


def test_category_detail_omits_subcategory_stack_chart_when_none_exist(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-10", type="expense", category="Groceries", amount="50"
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "by subcategory</h2>" not in response.text
    assert "cat-subcat-chart-scroll" not in response.text


def test_category_detail_subcategory_stack_chart_collapses_overflow_into_other(client):
    _create_account(client)
    for i in range(11):
        _create_transaction(
            client,
            date="2026-01-10",
            type="expense",
            category="Groceries",
            subcategory=f"Sub{i}",
            amount=str(100 - i),
        )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    legend = response.text.split("by subcategory</h2>")[1].split("</ul>")[0]
    assert legend.count("pie-legend-item") == 10  # 9 named + "Other"
    assert "pie-slice-other" in legend


def test_category_detail_subcategory_filter_defaults_to_all_checked(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2026-01-11",
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="10",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    legend = response.text.split("pie-legend-filter")[1].split("</form>")[0]
    # "checked onchange" (the boolean attribute immediately followed by
    # the checkbox's own onchange handler) is specific to a checked box —
    # a bare "checked" substring also appears inside the "only" link's
    # own onclick JS (`c.checked = ...`), which isn't what this asserts.
    assert legend.count("checked onchange") == 2
    assert "Show all" not in response.text


def test_category_detail_subcategory_filter_narrows_the_chart(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2026-01-11",
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="10",
    )

    response = client.get(
        "/reports/category",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategories": "Farmers Market",
        },
    )

    assert response.status_code == 200
    assert "Show all" in response.text
    chart_svg = response.text.split("cat-subcat-chart-scroll")[1].split("</svg>")[0]
    assert "Supermarket" not in chart_svg
    assert "Farmers Market" in chart_svg
    # Farmers Market is the smaller (2nd-ranked) subcategory, so its color
    # class must stay pie-slice-1 even filtered down to just itself —
    # never recolored to pie-slice-0 just because it's now the only one.
    assert "pie-slice-1" in chart_svg
    assert "pie-slice-0" not in chart_svg


def test_category_detail_subcategory_filter_ignores_unknown_names(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )

    response = client.get(
        "/reports/category",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategories": "NotARealSubcategory",
        },
    )

    assert response.status_code == 200
    # An entirely-invalid selection falls back to showing everything
    # rather than a confusingly empty chart.
    assert "Show all" not in response.text
    chart_svg = response.text.split("cat-subcat-chart-scroll")[1].split("</svg>")[0]
    assert "Supermarket" in chart_svg


def test_category_detail_subcategory_filter_round_trips_account_and_type(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2026-01-11",
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="10",
    )

    response = client.get(
        "/reports/category",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "account_id": "chk",
            "subcategories": "Supermarket",
        },
    )

    assert response.status_code == 200
    legend = response.text.split("pie-legend-filter")[1].split("</form>")[0]
    assert 'name="txn_type" value="expense"' in legend
    assert 'name="category" value="Groceries"' in legend
    assert 'name="account_id" value="chk"' in legend


def test_category_detail_subcategory_only_link_targets_single_subcategory(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2026-01-11",
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="10",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    # A progressive-enhancement fallback: clicking "only" without JS still
    # navigates straight to that single subcategory via its href.
    assert (
        "&subcategories=Supermarket" in response.text
        or "&subcategories=Farmers+Market" in response.text
    )
    assert response.text.count("pie-legend-only") == 2


def test_subcategory_detail_rejects_transfer_type(client):
    response = client.get(
        "/reports/subcategory",
        params={
            "txn_type": "transfer",
            "category": "Transfer",
            "subcategory": "Transfer",
        },
    )

    assert response.status_code == 404


def test_subcategory_detail_shows_no_data_message_for_unused_subcategory(client):
    response = client.get(
        "/reports/subcategory",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )

    assert response.status_code == 200
    assert "No transactions in this subcategory yet" in response.text


def test_subcategory_detail_shows_total_scoped_to_just_that_subcategory(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2026-01-11",
        type="expense",
        category="Groceries",
        subcategory="Farmers Market",
        amount="999",
    )

    response = client.get(
        "/reports/subcategory",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )

    assert response.status_code == 200
    assert "Supermarket" in response.text
    assert "50.00" in response.text
    assert "999.00" not in response.text


def test_subcategory_detail_year_and_month_scoping(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )
    _create_transaction(
        client,
        date="2025-06-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="30",
    )

    year_response = client.get(
        "/reports/subcategory/2026",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )
    assert year_response.status_code == 200
    assert "2026 total" in year_response.text
    assert "50.00" in year_response.text

    month_response = client.get(
        "/reports/subcategory/2026/1",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )
    assert month_response.status_code == 200
    assert "Jan 2026 total" in month_response.text
    assert "Top 1 transaction" in month_response.text

    invalid_month_response = client.get(
        "/reports/subcategory/2026/13",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )
    assert invalid_month_response.status_code == 404


def test_subcategory_detail_uses_its_own_budget_not_the_categorys(client):
    client.post("/categories/expense", data={"name": "Groceries", "budget": "500.00"})
    client.post(
        "/categories/expense/Groceries/subcategories",
        data={"name": "Supermarket", "budget": "40.00"},
    )
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client,
        date=today,
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="20",
    )

    response = client.get(
        "/reports/subcategory",
        params={
            "txn_type": "expense",
            "category": "Groceries",
            "subcategory": "Supermarket",
        },
    )

    assert response.status_code == 200
    assert "50% of budget" in response.text


def test_category_detail_subcategory_links_point_to_subcategory_report(client):
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client,
        date=today,
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="80",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert (
        "/reports/subcategory?txn_type=expense&category=Groceries"
        "&subcategory=Supermarket" in response.text
    )


def test_category_detail_hides_by_subcategory_section_when_none_exist(client):
    _create_account(client)
    _create_transaction(
        client, date="2026-01-10", type="expense", category="Groceries", amount="50"
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "By subcategory" not in response.text
    assert "report-columns" not in response.text


def test_category_detail_shows_by_subcategory_section_when_any_exist(client):
    _create_account(client)
    _create_transaction(
        client,
        date="2026-01-10",
        type="expense",
        category="Groceries",
        subcategory="Supermarket",
        amount="50",
    )

    response = client.get(
        "/reports/category", params={"txn_type": "expense", "category": "Groceries"}
    )

    assert response.status_code == 200
    assert "By subcategory" in response.text
    assert "report-columns" in response.text


def test_reports_overview_omits_heatmap_when_no_expenses(client):
    response = client.get("/reports")

    assert response.status_code == 200
    assert "Spending heatmap" not in response.text


def test_reports_overview_shows_spending_heatmap_for_top_categories(client):
    _create_account(client)
    today = date.today().isoformat()
    _create_transaction(
        client, date=today, type="expense", category="Groceries", amount="80"
    )
    _create_transaction(
        client, date=today, type="expense", category="Rent", amount="1500"
    )

    response = client.get("/reports")

    assert response.status_code == 200
    assert "Spending heatmap" in response.text
    assert "top-cat-heatmap" in response.text
    assert "Groceries" in response.text
    assert "Rent" in response.text
    assert "heatmap-fill" in response.text


def test_reports_overview_heatmap_cell_links_to_that_category_and_month(client):
    _create_account(client)
    today = date.today()
    _create_transaction(
        client,
        date=today.isoformat(),
        type="expense",
        category="Groceries",
        amount="80",
    )

    response = client.get("/reports")

    assert response.status_code == 200
    last_day = monthrange(today.year, today.month)[1]
    month_from = f"{today.year:04d}-{today.month:02d}-01"
    month_to = f"{today.year:04d}-{today.month:02d}-{last_day:02d}"
    assert (
        f"date_from={month_from}&amp;date_to={month_to}&amp;category=Groceries"
        f"&amp;subcategory=&amp;txn_type=expense" in response.text
    )


def test_reports_overview_heatmap_round_trips_account_filter(client):
    _create_account(client, "chk")
    _create_transaction(
        client,
        account_id="chk",
        date=date.today().isoformat(),
        type="expense",
        category="Groceries",
        amount="80",
    )

    response = client.get("/reports", params={"account_id": "chk"})

    assert response.status_code == 200
    assert "account_id=chk" in response.text
