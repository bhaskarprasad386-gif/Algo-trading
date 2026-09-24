from datetime import date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.cash_future_strategy_routes import router
from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.core.config import settings


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
            "target": 5.0,
            "stop_loss": 2.0,
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


def test_strategy_run_route_returns_output_analysis():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "target": 5.0,
            "points": [
                payload(gap=10, timestamp=start),
                payload(gap=4, timestamp=start + timedelta(hours=1)),
            ],
        },
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["initial_capital"] == 10_000_000
    assert analysis["final_equity"] == 10_000_600.0
    assert analysis["net_pnl"] == 600.0
    assert analysis["roi"] == 0.00006
    assert analysis["trade_count"] == 1
    assert analysis["wins"] == 1
    assert analysis["losses"] == 0
    assert analysis["profit_factor"] is None
    assert analysis["monthly_pnl"] == {"2026-09": 600.0}
    assert analysis["yearly_pnl"] == {"2026": 600.0}


def test_strategy_run_result_page_returns_bounded_records_and_cursor(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    ledger = BacktestLedger(ledger_db)
    ledger.start_run("page-route", "strategy", "1", 100_000.0)
    for index in range(3):
        ledger.append(LedgerRecord("page-route", "equity", index, {"value": index}))

    from app.backtesting.cash_future_strategy_routes import _result_page

    first = _result_page(ledger, "page-route", "equity", 2, None)
    assert [record["value"] for record in first["data"]] == [0, 1]
    assert first["next_cursor"] is not None
    assert first["total"] == 3

    second = _result_page(
        ledger, "page-route", "equity", 2, first["next_cursor"]
    )
    assert [record["value"] for record in second["data"]] == [2]
    assert second["next_cursor"] is None
    ledger.close()


def test_strategy_run_result_page_rejects_unknown_run():
    ledger = BacktestLedger()
    from app.backtesting.cash_future_strategy_routes import _result_page

    try:
        try:
            _result_page(ledger, "missing", "equity", 10, None)
        except ValueError as exc:
            assert "unknown run_id" in str(exc)
        else:
            raise AssertionError("unknown run must fail")
    finally:
        ledger.close()


def test_strategy_run_persists_provenance_for_direct_points(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    start = datetime(2026, 9, 2, 10, 0)
    request = {
        "strategy_id": "gap_threshold",
        "strategy_version": "1",
        "initial_capital": 10_000_000,
        "points": [payload(gap=10, timestamp=start), payload(gap=4, timestamp=start + timedelta(hours=1))],
        "stop_loss": 2.0,
        "target": 5.0,
    }
    response = client().post("/api/v1/backtesting/cash-future/strategy-run", json=request)
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    ledger = BacktestLedger(ledger_db)
    try:
        metadata = ledger.run_metadata(run_id)
        assert metadata is not None
        assert len(metadata["strategy_hash"]) == 64
        assert len(metadata["data_source_fingerprint"]) == 64
        assert len(metadata["metadata"]["strategy_config_hash"]) == 64
    finally:
        ledger.close()
