from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_legacy_cash_future_dashboard_is_not_mounted():
    response = client.get("/dashboard")
    assert response.status_code == 404


def test_broker_settings_page_remains_available():
    response = client.get("/dashboard/broker")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
