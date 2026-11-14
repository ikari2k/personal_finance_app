"""Integration tests for the 50/30/20 report router."""

from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.models.transaction import Transaction, TransactionType
from app.storage.accounts import write_accounts
from app.storage.budget_rule import read_targets
from app.storage.categories import write_categories
from app.storage.ledger import read_ledger, write_ledger


def _seed():
    write_accounts(
        [
            Account(id="chk", name="Chk", number="1", starting_balance=Decimal("0")),
            Account(
                id="sav",
                name="Sav",
                number="2",
                starting_balance=Decimal("0"),
                account_type=AccountType.SAVINGS,
            ),
        ]
    )
    write_categories(
        {
            "income": {
                "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
            },
            "expense": {
                "Rent": {
                    "icon": "",
                    "budget": "",
                    "bucket": "need",
                    "subcategories": {},
                },
                "Misc": {"icon": "", "budget": "", "bucket": "", "subcategories": {}},
            },
        }
    )

    def t(id, amount, type_, category="", account="chk"):
        return Transaction(
            id=id,
            date=date(2026, 3, 5),
            account_id=account,
            category=category,
            subcategory="",
            description="d",
            amount=Decimal(amount),
            type=type_,
        )

    write_ledger(
        [
            t("a", "1000", TransactionType.INCOME, "Salary"),
            t("b", "-500", TransactionType.EXPENSE, "Rent"),
            t("c", "-100", TransactionType.EXPENSE, "Misc"),
            t("d", "200", TransactionType.TRANSFER, account="sav"),
            t("e", "-200", TransactionType.TRANSFER),
        ]
    )


def test_page_renders_with_empty_data(client):
    response = client.get("/reports/budget-rule")

    assert response.status_code == 200
    assert "50/30/20" in response.text


def test_page_shows_split_for_month(client):
    _seed()

    response = client.get("/reports/budget-rule?month=2026-03")

    assert response.status_code == 200
    assert "March 2026" in response.text
    assert "50.0%" in response.text  # needs
    assert "20.0%" in response.text  # savings
    assert "classify these" in response.text  # Misc is unclassified


def test_invalid_month_is_404(client):
    assert client.get("/reports/budget-rule?month=nope").status_code == 404
    assert client.get("/reports/budget-rule?month=2026-13").status_code == 404


def test_edit_form_prefilled_with_defaults(client):
    response = client.get("/reports/budget-rule/targets/edit")

    assert 'name="needs"' in response.text and 'value="50"' in response.text


def test_save_targets_persists_and_closes_dialog(client):
    _seed()

    response = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "60", "wants": "20", "savings": "20", "month": "2026-03"},
    )

    assert response.status_code == 200
    assert read_targets().needs == 60
    assert "close-dialog" in response.headers["HX-Trigger"]
    assert 'hx-swap-oob="true"' in response.text


def test_save_targets_rejects_bad_sum_and_non_numbers(client):
    bad_sum = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "60", "wants": "30", "savings": "30"},
    )
    assert "sum to 100" in bad_sum.text
    assert "HX-Trigger" not in bad_sum.headers

    not_numbers = client.post(
        "/reports/budget-rule/targets",
        data={"needs": "a", "wants": "30", "savings": "20"},
    )
    assert "whole numbers" in not_numbers.text
    assert read_targets().needs == 50


def test_rolling_windows_have_tabs_and_panels(client):
    _seed()

    text = client.get("/reports/budget-rule?month=2026-03").text

    for label in ("Last 3 mo", "Last 6 mo", "Last 12 mo"):
        assert label in text
    assert "Last 3 months to March 2026" in text
    assert 'data-panel="r12"' in text


def test_rolling_window_excludes_older_months(client):
    _seed()  # all activity in March 2026

    text = client.get("/reports/budget-rule?month=2026-07").text

    # March is outside a 3-month window ending July, inside a 6-month one.
    panel3 = text.split('data-panel="r3"')[1].split("</section>")[0]
    panel6 = text.split('data-panel="r6"')[1].split("</section>")[0]
    assert "No income in this period" in panel3
    assert "No income in this period" not in panel6


def test_month_navigation_controls(client):
    _seed()

    past = client.get("/reports/budget-rule?month=2026-03").text
    assert 'name="month"' in past and 'value="2026-03"' in past
    assert "Back to current month" in past
    assert "Previous: February 2026" in past
    assert "Next: April 2026" in past

    from datetime import date

    today = date.today()
    current = client.get(
        f"/reports/budget-rule?month={today.year:04d}-{today.month:02d}"
    ).text
    assert "Back to current month" not in current
    assert 'class="step disabled"' in current


def test_unclassified_row_hidden_when_zero(client):
    _seed()  # Misc spend is unclassified in March only

    def month_panel(month):
        text = client.get(f"/reports/budget-rule?month={month}").text
        return text.split('data-panel="month"')[1].split("</section>")[0]

    assert "br-swatch-unclassified" in month_panel("2026-03")
    assert "br-swatch-unclassified" not in month_panel("2026-05")


def test_page_has_non_sticky_section_index(client):
    text = client.get("/reports/budget-rule").text

    assert 'class="section-index static"' in text
    assert 'id="breakdown"' in text and 'id="trend"' in text


def _write_month(year, month, *, needs, wants, savings, today_override=None):
    """One closed month: 1000 income plus the given needs/wants/savings."""
    write_accounts(
        [
            Account(id="chk", name="Chk", number="1", starting_balance=Decimal("0")),
            Account(
                id="sav",
                name="Sav",
                number="2",
                starting_balance=Decimal("0"),
                account_type=AccountType.SAVINGS,
            ),
        ]
    )
    write_categories(
        {
            "income": {
                "Salary": {"icon": "", "budget": "", "bucket": "", "subcategories": {}}
            },
            "expense": {
                "Rent": {
                    "icon": "",
                    "budget": "",
                    "bucket": "need",
                    "subcategories": {},
                },
                "Fun": {
                    "icon": "",
                    "budget": "",
                    "bucket": "want",
                    "subcategories": {},
                },
            },
        }
    )

    def t(id_, amount, type_, category="", account="chk", transfer_id=None):
        return Transaction(
            id=id_,
            date=date(year, month, 5),
            account_id=account,
            category=category,
            subcategory="",
            description="d",
            amount=Decimal(amount),
            type=type_,
            transfer_id=transfer_id,
        )

    write_ledger(
        [
            t("i", "1000", TransactionType.INCOME, "Salary"),
            t("n", f"-{needs}", TransactionType.EXPENSE, "Rent"),
            t("w", f"-{wants}", TransactionType.EXPENSE, "Fun"),
            t("s1", str(savings), TransactionType.TRANSFER, "Transfer", "sav", "T"),
            t("s2", f"-{savings}", TransactionType.TRANSFER, "Transfer", "chk", "T"),
        ]
    )


def test_closed_month_that_met_the_rule_gets_the_seal(client):
    _write_month(2026, 3, needs=500, wants=300, savings=200)

    text = client.get("/reports/budget-rule?month=2026-03").text

    month_panel = text.split('data-panel="month"')[1].split("</section>")[0]
    assert "Rule met" in month_panel and "br-bucket-row" in month_panel
    assert "br-trend-check" in text  # check mark on the trend bar


def test_panel_shows_each_bucket_even_when_one_runs_far_past_the_scale(client):
    _write_month(2026, 3, needs=5000, wants=300, savings=200)

    text = client.get("/reports/budget-rule?month=2026-03").text

    panel = text.split('data-panel="month"')[1].split("</section>")[0]
    assert "br-bucket-track is-cut" in panel
    assert panel.count("br-bucket-row") == 3  # Needs, Wants, Savings
    assert "br-wants-bg" in panel and "br-savings-bg" in panel


def test_trend_has_a_panel_per_bucket_and_tears_clipped_bars(client):
    _write_month(2026, 3, needs=5000, wants=300, savings=200)

    text = client.get("/reports/budget-rule?month=2026-03").text

    trend = text.split('class="br-trend"')[1].split("</svg>")[0]
    for name in ("Needs", "Wants", "Savings"):
        assert f">{name}</text>" in trend
    assert "target 50%" in trend and "br-trend-target" in trend
    assert "br-trend-tear" in trend  # needs at 500% runs past the scale
    assert ">500<" in trend  # the figure stays real
    assert "Unclassified</text>" not in trend  # none anywhere, so no panel


def test_closed_month_that_missed_the_rule_has_no_seal(client):
    _write_month(2026, 3, needs=600, wants=300, savings=100)

    text = client.get("/reports/budget-rule?month=2026-03").text

    month_panel = text.split('data-panel="month"')[1].split("</section>")[0]
    assert "Rule met" not in month_panel
    assert "br-trend-check" not in text


def test_month_in_progress_never_gets_the_seal(client):
    today = date.today()
    _write_month(today.year, today.month, needs=500, wants=300, savings=200)

    text = client.get(
        f"/reports/budget-rule?month={today.year:04d}-{today.month:02d}"
    ).text

    month_panel = text.split('data-panel="month"')[1].split("</section>")[0]
    assert "Rule met" not in month_panel


def _last_month():
    from datetime import date as _date

    today = _date.today()
    return (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)


def _dashboard_widget(client) -> str:
    return client.get("/dashboard").text.split("Last 6 months")[1].split("</svg>")[0]


def test_dashboard_widget_is_the_per_bucket_trend(client):
    _write_month(*_last_month(), needs=500, wants=300, savings=200)

    widget = _dashboard_widget(client)

    for name in ("Needs", "Wants", "Savings"):
        assert f">{name}</text>" in widget
    assert "br-trend-target" in widget


def test_dashboard_marks_met_months_with_a_check(client):
    _write_month(*_last_month(), needs=500, wants=300, savings=200)

    assert "br-trend-check" in _dashboard_widget(client)


def test_dashboard_reddens_the_figures_that_missed(client):
    _write_month(*_last_month(), needs=550, wants=300, savings=150)

    widget = _dashboard_widget(client)

    assert "br-trend-check" not in widget
    assert 'br-trend-val is-miss">55<' in widget  # needs over 50
    assert 'br-trend-val is-miss">15<' in widget  # savings under 20


def test_dashboard_tears_a_month_far_past_the_scale(client):
    _write_month(*_last_month(), needs=5000, wants=100, savings=0)

    widget = _dashboard_widget(client)

    assert "br-trend-tear" in widget
    assert ">500<" in widget  # the label keeps the real figure


def _expense_only_this_month():
    """Spend in the current month and nothing else: no income anywhere."""
    from datetime import date as _date

    today = _date.today()
    _write_month(today.year, today.month, needs=300, wants=100, savings=50)
    write_ledger([t for t in read_ledger() if t.type == TransactionType.EXPENSE])
    return today


def test_current_month_without_income_is_not_calculated(client):
    today = _expense_only_this_month()

    text = client.get(
        f"/reports/budget-rule?month={today.year:04d}-{today.month:02d}"
    ).text

    panel = text.split('data-panel="month"')[1].split("</section>")[0]
    assert "Waiting for income" in panel
    assert "br-bucket-row" not in panel and "breakdown-table" not in panel


def test_month_report_waits_for_income_in_the_current_month(client):
    today = _expense_only_this_month()

    text = client.get(f"/reports/{today.year}/{today.month}").text

    assert "Waiting for income" in text


def test_every_bucket_fill_class_the_bars_use_is_styled():
    from pathlib import Path

    css = (Path(__file__).parents[2] / "app/static/style.css").read_text()
    for key in ("needs", "wants", "savings", "unclassified"):
        assert f".br-{key}-bg" in css


def test_reports_rolling_windows_show_bars_per_bucket(client):
    _write_month(*_last_month(), needs=500, wants=300, savings=200)

    text = client.get("/reports").text

    for label in ("Last 3 months", "Last 6 months", "Last 12 months"):
        assert label in text
    assert text.count("br-window") >= 3
    assert "br-buckets br-buckets-narrow" in text


def test_month_report_title_is_sealed_when_the_month_met_the_rule(client):
    _write_month(2026, 3, needs=500, wants=300, savings=200)

    text = client.get("/reports/2026/3").text

    title = text.split('class="report-title')[1].split("</div>")[0]
    assert title.startswith(" is-met") and "Rule met" in title


def test_month_report_title_plain_when_the_rule_was_missed(client):
    _write_month(2026, 3, needs=600, wants=300, savings=100)

    text = client.get("/reports/2026/3").text

    title = text.split('class="report-title')[1].split("</div>")[0]
    assert not title.startswith(" is-met") and "Rule met" not in title


def test_year_report_marks_only_the_met_months(client):
    _write_month(2026, 3, needs=500, wants=300, savings=200)

    text = client.get("/reports/2026").text

    table = text.split('id="months"')[1].split("</table>")[0]
    assert table.count('class="is-met"') == 1
    assert "March 2026" in table.split('class="is-met"')[1]


def test_year_report_explains_the_tick_only_when_there_is_one(client):
    empty = client.get("/reports/2026").text
    assert "A closed month that stayed within" not in empty

    _write_month(2026, 3, needs=500, wants=300, savings=200)
    text = client.get("/reports/2026").text

    assert "A closed month that stayed within the 50/30/20 targets" in text
    assert "needs up to 50%" in text and "savings of at least 20%" in text
