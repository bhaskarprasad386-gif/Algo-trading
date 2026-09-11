from datetime import date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.cash_future_strategy_routes import router


def payload(*, gap: float, timestamp: datetime, contract_month: str = "SEP"):
    return {
        "timestamp": timestamp.isoformat(),
        "symbol": "ABC",
        "contract_month": contract_month,
        "cash_price": 100.0,
        "future_price": 100.0 + gap,
        "gap": gap,
        "gap_pct": gap,
        "lot_size": 100,
        "margin_required": 10000.0,
        "expiry_date": "2026-09-30",
    }


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_strategy_run_route_executes_historical_buy_sell():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "points": [
                payload(gap=10, timestamp=start),
                payload(gap=4, timestamp=start + timedelta(hours=1)),
                payload(gap=3, timestamp=start + timedelta(days=1)),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["strategy_id"] == "gap_threshold"
    assert body["signal_count"] == 2
    assert body["trade_count"] == 1
    assert body["net_profit"] == 600.0


def test_strategy_run_route_rejects_unknown_strategy():
    now = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "does_not_exist",
            "points": [payload(gap=10, timestamp=now)],
        },
    )
    assert response.status_code == 404
    assert "unknown Cash-Future strategy" in response.json()["detail"]


def test_strategy_run_route_preserves_contract_isolation_error():
    now = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "points": [
                payload(gap=10, timestamp=now, contract_month="SEP"),
                payload(gap=-1, timestamp=now + timedelta(hours=1), contract_month="OCT"),
            ],
        },
    )
    assert response.status_code == 422
    assert "multiple contract months" in response.json()["detail"]
