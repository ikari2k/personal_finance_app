"""The account choice is shared between Reports, Dashboard and Transactions."""


def _accounts(client):
    for account_id in ("chk", "sav"):
        client.post(
            "/accounts",
            data={
                "account_id": account_id,
                "name": account_id.upper(),
                "number": account_id,
                "description": "",
                "starting_balance": "0",
                "status": "active",
            },
        )


def test_reports_use_the_remembered_account(client):
    _accounts(client)
    client.get("/reports?account_id=sav")  # remembered via the shared cookie

    text = client.get("/reports").text

    assert '<option value="sav" selected>' in text


def test_explicit_empty_account_overrides_cookie_and_is_remembered(client):
    _accounts(client)
    client.get("/reports?account_id=sav")

    response = client.get("/reports?account_id=")

    assert '<option value="" selected>' in response.text
    # ...and "all accounts" is what's remembered now, not the old choice.
    assert '<option value="" selected>' in client.get("/reports").text


def test_choice_on_reports_carries_over_to_transactions(client):
    _accounts(client)

    client.get("/reports/2026?account_id=chk")
    text = client.get("/transactions").text

    assert '<option value="chk" selected>' in text


def test_choice_on_transactions_carries_over_to_reports(client):
    _accounts(client)

    client.get("/transactions?account_id=sav")

    assert '<option value="sav" selected>' in client.get("/reports/2026/3").text


def test_scope_bar_account_change_keeps_other_query_params(client):
    _accounts(client)

    text = client.get(
        "/reports/category?txn_type=expense&category=Food&account_id=chk"
    ).text

    bar = text.split('class="view-options scope-bar"')[1].split("</form>")[0]
    assert 'name="txn_type" value="expense"' in bar
    assert 'name="category" value="Food"' in bar
    assert 'name="account_id" value=' not in bar  # the select owns account_id


def test_transactions_toolbar_no_longer_has_its_own_account_select(client):
    _accounts(client)

    text = client.get("/transactions").text

    assert text.count('name="account_id"') == 1  # only the scope bar's
    assert 'class="view-options scope-bar"' in text


def test_budget_rule_page_has_month_stepper_in_scope_bar(client):
    text = client.get("/reports/budget-rule?month=2026-03").text

    bar = text.split('class="view-options scope-bar"')[1].split("</main>")[0]
    assert 'class="period-stepper"' in bar
    assert 'name="account_id"' not in text.split('id="breakdown"')[0]
