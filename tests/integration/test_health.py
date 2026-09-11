"""Integration test for the Phase 0 health-check endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok_page() -> None:
    """The /health endpoint should respond 200 with the rendered page."""
    response = client.get("/health")

    assert response.status_code == 200
    assert "OK" in response.text
