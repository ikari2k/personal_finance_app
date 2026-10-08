"""The scope month: set on report pages, adopted by Transactions as a date range."""


def _range_is(text, start, end):
    """The list's From/To inputs hold exactly this range."""
    return (
        f'name="date_from" value="{start}"' in text
        and f'name="date_to" value="{end}"' in text
    )


def _txn_page(client, query=""):
    return client.get(f"/transactions{query}").text


def test_explicit_scope_month_sets_date_range_and_bar(client):
    text = _txn_page(client, "?scope_month=2026-03")

    assert _range_is(text, "2026-03-01", "2026-03-31")
    assert "Remove filter: 2026-03" not in text  # the bar already shows the month
    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert 'value="2026-03"' in bar and 'name="scope_month"' in bar
    assert "Previous: 2026-02" in bar


def test_month_chosen_on_a_report_is_adopted_once_by_transactions(client):
    client.get("/reports/2026/3")

    first = _txn_page(client)
    assert _range_is(first, "2026-03-01", "2026-03-31")

    # The user then narrows to their own range: it must not be overridden.
    client.get("/transactions?date_from=2026-01-05&date_to=2026-01-20")
    again = _txn_page(client)
    assert "2026-01-05 – 2026-01-20" in again
    assert "2026-03-01" not in again


def test_new_scope_month_is_adopted_again(client):
    client.get("/reports/2026/3")
    _txn_page(client)
    client.get("/transactions?date_from=&date_to=")  # user clears the range

    client.get("/reports/2026/4")
    text = _txn_page(client)

    assert _range_is(text, "2026-04-01", "2026-04-30")


def test_clearing_the_range_is_respected_until_scope_month_changes(client):
    client.get("/reports/2026/3")
    _txn_page(client)

    cleared = client.get("/transactions?date_from=&date_to=").text
    later = _txn_page(client)

    assert "Remove filter: 2026-03" not in cleared
    assert "Remove filter: 2026-03" not in later


def test_custom_range_shows_empty_month_picker_without_arrows(client):
    text = _txn_page(client, "?date_from=2026-01-05&date_to=2026-01-20")

    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert 'name="scope_month" value=""' in bar
    assert bar.count('class="step disabled"') == 2


def test_budget_rule_defaults_to_the_remembered_month(client):
    client.get("/reports/2026/3")

    text = client.get("/reports/budget-rule").text

    assert 'value="2026-03"' in text
    assert "March 2026" in text


def test_budget_rule_visit_records_the_month_for_transactions(client):
    client.get("/reports/budget-rule?month=2026-05")

    assert _range_is(_txn_page(client), "2026-05-01", "2026-05-31")


def test_breadcrumb_comes_before_the_scope_bar(client):
    text = client.get("/reports/2026/3").text

    assert text.index('class="breadcrumb"') < text.index(
        'class="view-options scope-bar"'
    )


def test_custom_range_still_gets_a_chip(client):
    text = _txn_page(client, "?date_from=2026-01-05&date_to=2026-01-20")

    assert "Remove filter: 2026-01-05 – 2026-01-20" in text
