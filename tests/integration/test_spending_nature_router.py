"""Integration tests for the fixed-vs-flexible card, page and review dialog."""

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

    response = client.post(
        "/reports/fixed-flexible/apply",
        data={
            "pick": ["0", "1", "2", "3"],
            "row_category": ["Housing", "Nope", "Housing", "Housing"],
            "row_subcategory": ["Rent", "", "", "Rent"],
            "row_nature": ["fixed", "fixed", "bogus", "flexible"],
        },
    )
    # index 3 reuses ("Housing", "Rent") and is applied last, so it wins.
    assert response.status_code == 200
    assert read_nature_tags() == {("Housing", "Rent"): "flexible"}
    assert "Applied 2 tags" in response.headers["HX-Trigger"]
    assert 'hx-swap-oob="true"' in response.text
    assert config.CATEGORIES_PATH.read_text() == categories_before


def test_apply_lets_the_user_change_the_suggested_nature(client):
    _setup(client)

    client.post(
        "/reports/fixed-flexible/apply",
        data={
            "pick": ["0"],
            "row_category": ["Housing"],
            "row_subcategory": ["Rent"],
            "row_nature": ["semi"],
        },
    )

    assert read_nature_tags() == {("Housing", "Rent"): "semi"}


def test_inline_tag_select_sets_changes_and_clears_a_tag(client):
    _setup(client)
    form = {"category": "Housing", "subcategory": "Rent", "account_id": ""}

    client.post("/reports/fixed-flexible/tag", data={**form, "nature": "fixed"})
    assert read_nature_tags() == {("Housing", "Rent"): "fixed"}

    client.post("/reports/fixed-flexible/tag", data={**form, "nature": "flexible"})
    assert read_nature_tags() == {("Housing", "Rent"): "flexible"}

    response = client.post("/reports/fixed-flexible/tag", data={**form, "nature": ""})
    assert read_nature_tags() == {}
    assert response.status_code == 200


def test_inline_tag_rejects_unknown_category_and_nature(client):
    _setup(client)

    client.post(
        "/reports/fixed-flexible/tag",
        data={"category": "Nope", "subcategory": "", "nature": "fixed"},
    )
    client.post(
        "/reports/fixed-flexible/tag",
        data={"category": "Housing", "subcategory": "Rent", "nature": "bogus"},
    )

    assert read_nature_tags() == {}


def test_inline_tag_keeps_show_flexible_state(client):
    _setup(client)
    data = {"category": "Housing", "subcategory": "Rent", "nature": "fixed"}

    shown = client.post(
        "/reports/fixed-flexible/tag", data={**data, "show_flexible": "true"}
    )
    hidden = client.post(
        "/reports/fixed-flexible/tag", data={**data, "show_flexible": "false"}
    )

    assert 'class=""' in shown.text
    assert 'class="hide-flexible"' in hidden.text


def test_review_skips_suggestions_that_match_and_unticks_overrides(client):
    _setup(client)
    write_nature_tags({("Housing", "Rent"): "semi"})

    html = client.get("/reports/fixed-flexible/review").text

    # The user's own "semi" differs from the suggested "fixed": shown, not pre-ticked.
    assert "Housing / Rent" in html
    assert "checked" not in html

    write_nature_tags({("Housing", "Rent"): "fixed"})
    assert "No new suggestions" in client.get("/reports/fixed-flexible/review").text


def test_card_marks_year_boundary_in_a_twelve_month_view(client):
    _setup(client)
    html = client.get("/reports/fixed-flexible/card?months=12&account_id=").text
    today = date.today()

    if today.month == 12:  # a 12-month window ending in December has no boundary
        assert "ff-divider" not in html
    else:
        assert html.count("ff-divider") == 1
        assert f">{today.year - 1}<" in html and f">{today.year}<" in html
