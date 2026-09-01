from fastapi.testclient import TestClient

from app.main import app


def test_market_data_websocket_route_exists():
    routes = {getattr(route, "path", None) for route in app.routes}
    assert "/ws/market-data/{symbol}" in routes


def test_health_endpoint_contract():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "database" in payload
