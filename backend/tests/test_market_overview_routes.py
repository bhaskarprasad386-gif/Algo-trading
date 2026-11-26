import pytest

from app.market_data import routes


@pytest.fixture(autouse=True)
def reset_dashboard_route_caches():
    """Keep endpoint-cache behavior isolated across route tests."""
    for endpoint in (routes.get_market_overview, routes.get_live_data_health):
        endpoint.snapshot_cache.clear()
    with routes._OVERVIEW_HISTORICAL_CACHE_LOCK:
        routes._OVERVIEW_HISTORICAL_CACHE.clear()
    with routes._OVERVIEW_QUOTE_CACHE_LOCK:
        routes._OVERVIEW_QUOTE_CACHE.clear()
        routes._OVERVIEW_QUOTE_FAILURES.clear()
    with routes._CLOSED_OVERVIEW_CACHE_LOCK:
        routes._CLOSED_OVERVIEW_CACHE.clear()
    yield
    for endpoint in (routes.get_market_overview, routes.get_live_data_health):
        endpoint.snapshot_cache.clear()


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
    monkeypatch.setattr(routes, "_mcx_market_is_open", lambda now: True)

    result = routes.get_market_overview()

    assert result["status"] == "success"
    assert result["nse_market_session"] == "OPEN"
    assert result["mcx_market_session"] == "OPEN"
    assert {row["symbol"] for row in result["indices"]} >= {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert {row["symbol"] for row in result["commodities"]} >= {"GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
    assert all(row["status"] == "LIVE" for row in result["indices"] + result["commodities"])
    assert result["errors"] == []


def test_closed_market_overview_labels_historical_close_not_live_quote(monkeypatch):
    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

    class FakeClient:
        def quote_many(self, exchange, instruments):
            raise AssertionError("closed market must not request a live quote")

    class FakeHistoricalDataClient:
        def __init__(self, client):
            pass

        def get_candles(self, **kwargs):
            return {"data": [
                ["2026-10-15", 118, 124, 117, 123, 1000],
                ["2026-10-16", 123, 125, 119, 120, 900],
            ]}

    routes._CLOSED_OVERVIEW_CACHE.clear()
    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)
    monkeypatch.setattr("app.market_data.historical.HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: False)
    monkeypatch.setattr(routes, "_mcx_market_is_open", lambda now: False)

    result = routes.get_market_overview()

    assert result["market_session"] == "CLOSED"
    assert result["indices"]
    assert all(row["status"] == "CLOSED_LAST_CLOSE" for row in result["indices"])
    assert all(row["ltp"] == 120.0 for row in result["indices"])
    assert all(row["close"] == 123.0 for row in result["indices"])


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
    # Keep this assertion independent of the wall-clock NSE session.
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    result = routes.get_live_data_health()
    assert result["status"] == "success"
    assert result["source"] == "angelone-live-1s"
    assert result["records"] == 1
    assert result["instruments"] == 1
    assert result["persisted"] is True
    # Persisted history must not imply that the in-memory runtime feed is receiving ticks.
    assert result["runtime_feed"]["status"] == "NO_DATA"
    assert result["runtime_feed"]["age_seconds"] is None
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

    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        lambda: FakeCommonManager().snapshot(),
    )
    result = routes.get_live_data_health()
    runtime_feed = result["runtime_feed"]
    assert runtime_feed["connect_failures"] == 2
    assert runtime_feed["last_connect_failure"]["error_type"] == "ConnectionError"
    assert runtime_feed["recovery_attempts"] == 4


def test_live_data_health_does_not_use_fresh_mcx_tick_to_mask_stale_nse_nfo(monkeypatch):
    import time

    class FakeCommonManager:
        def snapshot(self):
            now_ns = time.time_ns()
            return {
                "socket_groups": 1,
                "connected_groups": ["3:0"],
                "disconnected_groups": [],
                "ticks_received": 100,
                # A global/MCX timestamp is fresh, but the scanner-critical
                # NSE and NFO exchanges have not produced fresh ticks.
                "latest_tick_timestamp_ns": now_ns,
                "latest_tick_timestamp_ns_by_exchange_type": {
                    "5": now_ns,
                    "1": now_ns - 60_000_000_000,
                    "2": now_ns - 60_000_000_000,
                },
                "last_tick": {"exchange_type": 5, "timestamp_ns": now_ns},
            }

    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        lambda: FakeCommonManager().snapshot(),
    )
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    result = routes.get_live_data_health()

    runtime_feed = result["runtime_feed"]
    assert runtime_feed["status"] == "STALE"
    assert runtime_feed["exchange_freshness"]["NSE"]["status"] == "STALE"
    assert runtime_feed["exchange_freshness"]["NFO"]["status"] == "STALE"
    assert runtime_feed["required_exchange_types"] == [1, 2]


def test_market_overview_reuses_quote_batch_between_dashboard_polls(monkeypatch):
    calls = []

    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

    class FakeClient:
        def quote_many(self, exchange, instruments):
            calls.append((exchange, tuple(item["symboltoken"] for item in instruments)))
            return {"data": {"fetched": [
                {"symbolToken": item["symboltoken"], "ltp": 100.0, "close": 99.0}
                for item in instruments
            ]}}

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    monkeypatch.setattr(routes, "_mcx_market_is_open", lambda now: True)

    first = routes.get_market_overview()
    assert len(calls) == 3
    assert all(row["status"] == "LIVE" for row in first["indices"] + first["commodities"])

    # Simulate the browser's next 5-second poll while forcing only the outer
    # dashboard snapshot to refresh. The broker quote batch remains reusable.
    routes.get_market_overview.snapshot_cache.clear()
    second = routes.get_market_overview()

    assert len(calls) == 3
    assert second["indices"][0]["quote_cache_age_seconds"] is not None
    assert all(row["status"] == "LIVE" for row in second["indices"] + second["commodities"])


def test_market_overview_uses_bounded_stale_quotes_after_broker_refresh_failure(monkeypatch):
    import time

    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

    class FakeClient:
        def __init__(self):
            self.fail = False

        def quote_many(self, exchange, instruments):
            if self.fail:
                raise RuntimeError("broker rate limited")
            return {"data": {"fetched": [
                {"symbolToken": item["symboltoken"], "ltp": 100.0, "close": 99.0}
                for item in instruments
            ]}}

    client = FakeClient()
    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", lambda: client)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    monkeypatch.setattr(routes, "_mcx_market_is_open", lambda now: True)

    first = routes.get_market_overview()
    assert all(row["status"] == "LIVE" for row in first["indices"] + first["commodities"])

    with routes._OVERVIEW_QUOTE_CACHE_LOCK:
        for key, (refreshed_at, quotes) in tuple(routes._OVERVIEW_QUOTE_CACHE.items()):
            routes._OVERVIEW_QUOTE_CACHE[key] = (
                time.monotonic() - routes._OVERVIEW_QUOTE_CACHE_TTL_SECONDS - 1.0,
                quotes,
            )
    routes.get_market_overview.snapshot_cache.clear()
    client.fail = True

    second = routes.get_market_overview()
    assert all(row["status"] == "STALE" for row in second["indices"] + second["commodities"])
    assert any("stale quote snapshot used" in item["error"] for item in second["errors"])
    assert all(
        row["quote_cache_age_seconds"] > routes._OVERVIEW_QUOTE_CACHE_TTL_SECONDS
        for row in second["indices"] + second["commodities"]
    )


def test_market_overview_backs_off_repeated_quote_failures(monkeypatch):
    calls = []

    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            return {"token": f"{exchange}-{symbol}"}

    class FailedClient:
        def quote_many(self, exchange, instruments):
            calls.append(exchange)
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FailedClient)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    monkeypatch.setattr(routes, "_mcx_market_is_open", lambda now: True)

    first = routes.get_market_overview()
    assert len(calls) == 3
    assert all(row["status"] == "NO_QUOTE" for row in first["indices"] + first["commodities"])

    routes.get_market_overview.snapshot_cache.clear()
    second = routes.get_market_overview()

    # Recent failures are negatively cached briefly so a 5-second browser poll
    # cannot hammer a broker endpoint that is already unavailable/rate-limited.
    assert len(calls) == 3
    assert all(row["status"] == "NO_QUOTE" for row in second["indices"] + second["commodities"])
