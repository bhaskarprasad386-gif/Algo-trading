from fastapi.testclient import TestClient

from app.main import app


def _collect_paths(routes):
    paths = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        nested = getattr(route, "routes", None)
        if nested:
            paths.update(_collect_paths(nested))
    return paths


def test_ltp_by_symbol_route_registered():
    paths = _collect_paths(app.routes)
    assert "/api/v1/market-data/ltp-by-symbol" in paths


def test_ltp_by_symbol_requires_symbol():
    client = TestClient(app)
    response = client.get("/api/v1/market-data/ltp-by-symbol")
    assert response.status_code == 422
