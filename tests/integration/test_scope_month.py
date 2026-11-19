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


def test_month_mode_offers_a_way_back_to_all_dates(client):
    text = _txn_page(client, "?scope_month=2026-03")

    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert 'href="/transactions?date_from=&amp;date_to="' in bar
    assert ">All dates</a>" in bar


def test_all_dates_link_returns_to_the_unfiltered_list(client):
    _txn_page(client, "?scope_month=2026-03")

    text = client.get("/transactions?date_from=&date_to=").text
    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]

    assert 'name="date_from" value=""' in text
    assert 'name="scope_month" value=""' in bar
    assert ">All dates</a>" not in bar  # nothing to leave when no month is set


def test_all_dates_stays_after_a_plain_revisit(client):
    _txn_page(client, "?scope_month=2026-03")
    client.get("/transactions?date_from=&date_to=")

    later = _txn_page(client)

    assert 'name="date_from" value=""' in later


def test_month_control_is_a_chooser_not_a_typed_input(client):
    text = _txn_page(client, "?scope_month=2026-03")

    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert "data-month-picker" in bar and 'class="month-pop"' in bar
    assert 'type="month"' not in bar
    assert 'type="hidden" name="scope_month" value="2026-03"' in bar
    assert "March 2026" in " ".join(bar.split())


def test_empty_month_control_invites_a_choice(client):
    text = _txn_page(client)

    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert "Choose month" in bar
    assert 'name="scope_month" value=""' in bar


def _grouping_value(text):
    """Which option of the grouping select is selected: (by_month, by_type)."""
    select = text.split('name="by_month"')[1].split("</select>")[0]
    selected = [line for line in select.splitlines() if " selected" in line][0]
    return (
        'value="true"' in selected,
        'data-by-type="true"' in selected,
    )


def test_month_mode_disables_the_date_range_inputs(client):
    text = _txn_page(client, "?scope_month=2026-03")

    assert 'aria-label="From date" disabled' in text
    assert 'aria-label="To date" disabled' in text


def test_date_range_inputs_are_enabled_outside_month_mode(client):
    for query in ("", "?date_from=2026-01-05&date_to=2026-01-20"):
        text = _txn_page(client, query)

        assert 'aria-label="From date" disabled' not in text
        assert 'aria-label="To date" disabled' not in text


def test_entering_month_mode_resets_grouping_to_the_default(client):
    client.get("/transactions?by_month=false&by_type=false")  # user chose no grouping

    text = _txn_page(client, "?scope_month=2026-03")

    assert _grouping_value(text) == (True, True)  # Month & type


def test_stepping_between_months_keeps_the_chosen_grouping(client):
    _txn_page(client, "?scope_month=2026-03")
    client.get(
        "/transactions?by_month=false&by_type=true&date_from=2026-03-01&date_to=2026-03-31"
    )

    text = _txn_page(client, "?scope_month=2026-02")

    assert _grouping_value(text) == (False, True)  # Type only survived the step
