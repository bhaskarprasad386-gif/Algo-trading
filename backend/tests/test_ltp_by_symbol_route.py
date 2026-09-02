from fastapi.testclient import TestClient

from app.main import app


def test_ltp_by_symbol_route_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/market-data/ltp-by-symbol" in paths


def test_ltp_by_symbol_requires_symbol():
    client = TestClient(app)
    response = client.get("/api/v1/market-data/ltp-by-symbol")
    assert response.status_code == 422
