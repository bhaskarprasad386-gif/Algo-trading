from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_retired_cash_future_backtest_routes_are_not_mounted():
    paths = (
        "/api/v1/scanner/cash-future/backtest/full/jobs",
        "/api/v1/scanner/cash-future/backtest/jobs/old-run",
        "/api/v1/scanner/cash-future/backtest/jobs/old-run/results",
    )
    for path in paths:
        response = client.get(path) if path.endswith("results") or path.endswith("old-run") else client.post(path)
        assert response.status_code == 404
