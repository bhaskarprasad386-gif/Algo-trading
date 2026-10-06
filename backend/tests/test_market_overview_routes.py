from app.market_data import routes


def test_market_overview_returns_index_and_commodity_groups(monkeypatch):
    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

    class FakeClient:
        def quote_many(self, exchange, instruments):
            return {
                "data": {
                    "fetched": [
                        {
                            "symbolToken": item["symboltoken"],
                            "ltp": 100.0,
                            "open": 99.0,
                            "high": 105.0,
                            "low": 95.0,
                            "close": 98.0,
                            "percentChange": 2.04,
                            "tradeVolume": 1000,
                            "opnInterest": 500,
                            "depth": {"buy": [{"price": 99.9}], "sell": [{"price": 100.1}]},
                        }
                        for item in instruments
                    ]
                }
            }

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)

    result = routes.get_market_overview()

    assert result["status"] == "success"
    assert {row["symbol"] for row in result["indices"]} >= {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert {row["symbol"] for row in result["commodities"]} >= {"GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
    assert all(row["status"] == "LIVE" for row in result["indices"] + result["commodities"])
    assert result["errors"] == []


def test_live_data_health_reports_persisted_coverage(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
    from app.market_data import routes

    db = tmp_path / "live.sqlite3"
    catalog = HistoricalCatalog(str(db))
    try:
        catalog.ingest(HistoricalRecord(
            source="angelone-live-1s",
            instrument="ABC|123",
            timeframe="1s",
            timestamp_ns=1_700_000_000_000_000_000,
            payload={"ltp": 100},
        ))
    finally:
        catalog.close()

    monkeypatch.setattr(settings, "BACKTEST_DATA_DB", str(db))
    result = routes.get_live_data_health()
    assert result["status"] == "success"
    assert result["source"] == "angelone-live-1s"
    assert result["records"] == 1
    assert result["instruments"] == 1
    assert result["persisted"] is True
    assert result["live_orders"] == "OFF"


def test_live_data_health_exposes_common_feed_recovery_telemetry(monkeypatch):
    class FakeCommonManager:
        def snapshot(self):
            return {
                "socket_groups": 0,
                "connected_groups": [],
                "disconnected_groups": ["1:0"],
                "connect_failures": 2,
                "last_connect_failure": {
                    "group": "1:0",
                    "error_type": "ConnectionError",
                    "error": "broker unavailable",
                    "failed_at_ns": 123,
                    "subscriptions": 1183,
                },
                "recovery_attempts": 4,
                "last_recovery_at": 456.0,
            }

    monkeypatch.setattr(routes, "shared_common_manager_snapshot", lambda: FakeCommonManager().snapshot())
    result = routes.get_live_data_health()
    assert result["common_feed"]["connect_failures"] == 2
    assert result["common_feed"]["last_connect_failure"]["error_type"] == "ConnectionError"
    assert result["common_feed"]["recovery_attempts"] == 4
