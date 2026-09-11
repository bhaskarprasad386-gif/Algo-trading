from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.cash_future_strategy_routes import router
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.core.config import settings
from app.backtesting.ledger import BacktestLedger


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_strategy_run_route_executes_from_durable_historical_catalog(monkeypatch, tmp_path):
    data_db = str(tmp_path / "data.db")
    contract_db = str(tmp_path / "contracts.db")
    ledger_db = str(tmp_path / "ledger.db")
    data = HistoricalCatalog(data_db)
    contracts = ContractMasterCatalog(contract_db)
    try:
        contracts.upsert_snapshot(
            date(2026, 1, 2),
            [ContractRecord("NFO", "ABC26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 75)],
        )
        data.ingest(
            [
                HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-05T03:45:00"), {"close": 100.0}),
                HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-05T03:46:00"), {"close": 100.0}),
                HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-05T03:45:00"), {"close": 110.0}),
                HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-05T03:46:00"), {"close": 98.0}),
            ]
        )
    finally:
        data.close()
        contracts.close()

    monkeypatch.setattr(settings, "BACKTEST_DATA_DB", data_db)
    monkeypatch.setattr(settings, "BACKTEST_CONTRACT_DB", contract_db)
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": "2026-01-05",
            "end_date": "2026-01-05",
            "spot_instrument": "NSE:1:ABC",
            "exchange": "NFO",
            "underlying": "ABC",
            "timeframe": "1m",
            "mode": "CURRENT",
            "source": "angelone",
            "initial_capital": 100000000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"].startswith("cash-future-")
    assert body["signal_count"] == 2
    assert body["trade_count"] == 1
    assert body["net_profit"] == 900.0

    ledger = BacktestLedger(ledger_db)
    try:
        run_id = body["run_id"]
        metadata = ledger.run_metadata(run_id)
        assert metadata["strategy_id"] == "gap_threshold"
        assert metadata["strategy_version"] == "1"
        assert len(ledger.records(run_id, "signal")) == 2
        assert len(ledger.records(run_id, "trade")) == 1
        assert len(ledger.records(run_id, "equity")) == 2
    finally:
        ledger.close()


def test_strategy_run_route_rejects_missing_historical_selection():
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={"strategy_id": "gap_threshold"},
    )

    assert response.status_code == 422
    assert "start_date and end_date are required" in str(response.json())
