from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_retired_cash_future_full_fno_backtest_start_is_gone():
    response = client.post(
        "/api/v1/backtesting/full-fno/start",
        json={"days": 365, "min_entry_gap": 5.0, "future_selection": "NEAR"},
    )
    assert response.status_code == 410


def test_retired_cash_future_full_fno_legacy_routes_are_gone():
    responses = (
        client.post("/api/v1/scanner/cash-future/backtest/full/jobs"),
        client.get("/api/v1/scanner/cash-future/backtest/jobs/retired"),
        client.get("/api/v1/scanner/cash-future/backtest/jobs/retired/results"),
        client.delete("/api/v1/scanner/cash-future/backtest/jobs/retired"),
        client.delete("/api/v1/scanner/cash-future/backtest/jobs/retired/results"),
    )
    assert [response.status_code for response in responses] == [410] * len(responses)
