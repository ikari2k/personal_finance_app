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

    assert "Remove filter: CHK" in text


def test_choice_on_transactions_carries_over_to_reports(client):
    _accounts(client)

    client.get("/transactions?account_id=sav")

    assert '<option value="sav" selected>' in client.get("/reports/2026/3").text
