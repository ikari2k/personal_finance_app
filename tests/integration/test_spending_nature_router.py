"""Integration tests for the fixed-vs-flexible card, page and review dialog."""

import json
from datetime import date

from app import config
from app.storage.expense_nature import read_nature_tags, write_nature_tags


def _setup(client):
    client.post(
        "/accounts",
        data={
            "account_id": "chk",
            "name": "Chk",
            "number": "1",
            "description": "",
            "starting_balance": "0",
        },
    )
    today = date.today()
    for back in range(8):
        month = today.month - back
        year = today.year
        while month < 1:
            month += 12
            year -= 1
        client.post(
            "/transactions",
            data={
                "account_id": "chk",
                "date": f"{year}-{month:02d}-03",
                "type": "expense",
                "category": "Housing",
                "subcategory": "Rent",
                "description": "LANDLORD RENT",
                "amount": "1000",
                "notes": "",
            },
        )
    client.post(
        "/transactions",
        data={
            "account_id": "chk",
            "date": f"{today.year}-{today.month:02d}-01",
            "type": "income",
            "category": "Salary",
            "subcategory": "",
            "description": "",
            "amount": "4000",
            "notes": "",
        },
    )


def test_reports_overview_shows_card_with_untagged_hint(client):
    _setup(client)

    html = client.get("/reports").text

    assert "Fixed vs flexible spending" in html
    assert "Nothing is tagged yet" in html
    assert "ff-untagged" in html


def test_card_fragment_range_and_fixed_share_of_income(client):
    _setup(client)
    write_nature_tags({("Housing", "Rent"): "fixed"})

    html = client.get("/reports/fixed-flexible/card?months=3&account_id=").text

    assert 'aria-selected="true"' in html
    assert "Fixed share of income" in html
    assert "75%" in html  # 3 months of 1,000 rent against one 4,000 salary
    assert "ff-fixed" in html


def test_card_fragment_unknown_range_falls_back(client):
    assert client.get("/reports/fixed-flexible/card?months=99").status_code == 200


def test_detail_page_lists_rent_as_fixed(client):
    _setup(client)

    html = client.get("/reports/fixed-flexible").text

    assert "Landlord Rent" in html
    assert "ff-pill-fixed" in html
    assert "Monthly" in html


def test_review_dialog_suggests_untagged_subcategory(client):
    _setup(client)

    html = client.get("/reports/fixed-flexible/review").text

    assert "Housing / Rent" in html
    assert "checked" in html
    assert "Unset" in html


def test_apply_writes_only_picked_valid_rows_and_leaves_categories_alone(client):
    _setup(client)
    categories_before = config.CATEGORIES_PATH.read_text()
    good = json.dumps(["Housing", "Rent", "fixed"])
    ghost = json.dumps(["Nope", "", "fixed"])
    bad_nature = json.dumps(["Housing", "", "bogus"])

    response = client.post(
        "/reports/fixed-flexible/apply", data={"pick": [good, ghost, bad_nature]}
    )

    assert response.status_code == 200
    assert read_nature_tags() == {("Housing", "Rent"): "fixed"}
    assert "close-dialog" in response.headers["HX-Trigger"]
    assert "Applied 1 tag" in response.headers["HX-Trigger"]
    assert 'hx-swap-oob="true"' in response.text
    assert config.CATEGORIES_PATH.read_text() == categories_before


def test_review_skips_suggestions_that_match_and_unticks_overrides(client):
    _setup(client)
    write_nature_tags({("Housing", "Rent"): "semi"})

    html = client.get("/reports/fixed-flexible/review").text

    # The user's own "semi" differs from the suggested "fixed": shown, not pre-ticked.
    assert "Housing / Rent" in html
    assert "checked" not in html

    write_nature_tags({("Housing", "Rent"): "fixed"})
    assert "No new suggestions" in client.get("/reports/fixed-flexible/review").text
