"""Integration test for the Phase 0 health-check endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok_page() -> None:
    """The /health endpoint should respond 200 with the rendered page."""
    response = client.get("/health")

    assert response.status_code == 200
    assert "OK" in response.text


def test_nav_marks_current_page_and_groups_manage_links(client):
    text = client.get("/reports/budget-rule").text

    assert '<a href="/reports" aria-current="page">Reports</a>' in text
    assert 'aria-current="page">Dashboard' not in text
    assert 'class="nav-manage"' in text

    categories = client.get("/categories/classify").text
    assert '<a href="/categories" aria-current="page">Categories</a>' in categories


def test_year_and_month_reports_use_shared_period_stepper(client):
    year = client.get("/reports/2026").text
    month = client.get("/reports/2026/3").text

    assert 'class="period-title">2026' in year and "Previous: 2025" in year
    assert 'class="period-title">March 2026' in month and "Next: April 2026" in month
    assert "scope-bar" in month
