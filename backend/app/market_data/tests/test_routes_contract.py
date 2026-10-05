from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_contract():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "database" in payload
