import pytest

from app.market_data import routes


@pytest.fixture(autouse=True)
def reset_dashboard_route_caches():
    """Keep endpoint-cache behavior isolated across route tests."""
    routes._LIVE_PERSISTED_SUMMARY_CACHE.clear()
    for endpoint in (routes.get_market_overview, routes.get_live_data_health, routes.get_common_feed_health):
        endpoint.snapshot_cache.clear()
    with routes._OVERVIEW_HISTORICAL_CACHE_LOCK:
        routes._OVERVIEW_HISTORICAL_CACHE.clear()
    with routes._OVERVIEW_QUOTE_CACHE_LOCK:
        routes._OVERVIEW_QUOTE_CACHE.clear()
        routes._OVERVIEW_QUOTE_FAILURES.clear()
    with routes._OVERVIEW_INSTRUMENT_CACHE_LOCK:
        routes._OVERVIEW_INSTRUMENT_CACHE.clear()
    with routes._CLOSED_OVERVIEW_CACHE_LOCK:
        routes._CLOSED_OVERVIEW_CACHE.clear()
    yield
    for endpoint in (routes.get_market_overview, routes.get_live_data_health, routes.get_common_feed_health):
        endpoint.snapshot_cache.clear()
    routes._LIVE_PERSISTED_SUMMARY_CACHE.clear()



def test_common_feed_health_reuses_bounded_runner_snapshot(monkeypatch):
    from app.core.config import settings

    feed_reads = []
    runner_reads = []

    def feed_snapshot():
        feed_reads.append(1)
        return {"snapshot_kind": "health-lite", "ticks_received": 42}

    def runner_snapshot():
        runner_reads.append(1)
        return {"status": "RUNNING", "updates": 17}

    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        feed_snapshot,
    )
    monkeypatch.setattr(routes, "_live_cash_future_runner_snapshot_provider", runner_snapshot)
    monkeypatch.setattr(settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", False)

    first = routes.get_common_feed_health()
    second = routes.get_common_feed_health()

    assert first["feed"]["snapshot_kind"] == "health-lite"
    assert second["cash_future_runner"]["updates"] == 17
    assert len(feed_reads) == 1
    assert len(runner_reads) == 1
    assert second["_snapshot_cache"]["age_seconds"] >= 0

def test_market_overview_fetches_only_nse_bse_indices(monkeypatch):
    calls = []

    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            calls.append(("resolve", exchange, symbol))
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            calls.append(("resolve", exchange, symbol))
            return {"token": f"{exchange}-{symbol}"}

    class FakeClient:
        def quote_many(self, exchange, instruments):
            calls.append(("quote", exchange, tuple(x["symboltoken"] for x in instruments)))
            return {"data": {"fetched": [
                {"symbolToken": item["symboltoken"], "ltp": 100.0, "close": 98.0}
                for item in instruments
            ]}}

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)

    result = routes.get_market_overview()

    assert result["status"] == "success"
    assert result["nse_market_session"] == "OPEN"
    assert {row["symbol"] for row in result["indices"]} >= {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert "commodities" not in result
    assert "mcx_market_session" not in result
    assert all(exchange in {"NSE", "BSE"} for kind, exchange, *_ in calls if kind == "quote")
    assert not any("MCX" in str(call) for call in calls)
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


def test_live_data_health_does_not_use_fresh_bse_tick_to_mask_stale_nse_nfo(monkeypatch):
    import time

    class FakeCommonManager:
        def snapshot(self):
            now_ns = time.time_ns()
            return {
                "socket_groups": 1,
                "connected_groups": ["3:0"],
                "disconnected_groups": [],
                "ticks_received": 100,
                # A global/BSE timestamp is fresh, but the scanner-critical
                # NSE and NFO exchanges have not produced fresh ticks.
                "latest_tick_timestamp_ns": now_ns,
                "latest_tick_timestamp_ns_by_exchange_type": {
                    "3": now_ns,
                    "1": now_ns - 60_000_000_000,
                    "2": now_ns - 60_000_000_000,
                },
                "last_tick": {"exchange_type": 3, "timestamp_ns": now_ns},
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
    resolutions = []

    class FakeMaster:
        def get_instrument(self, symbol, exchange):
            resolutions.append((exchange, symbol))
            return {"token": f"{exchange}-{symbol}"}

        def resolve_index_instrument(self, symbol, exchange):
            resolutions.append((exchange, symbol))
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

    first = routes.get_market_overview()
    assert len(calls) == 2
    assert "commodities" not in first
    assert all(row["status"] == "LIVE" for row in first["indices"])

    # Simulate the browser's next 5-second poll while forcing only the outer
    # dashboard snapshot to refresh. The broker quote batch remains reusable.
    routes.get_market_overview.snapshot_cache.clear()
    second = routes.get_market_overview()

    assert len(calls) == 2
    assert len(resolutions) == 6
    assert second["indices"][0]["quote_cache_age_seconds"] is not None
    assert all(row["status"] == "LIVE" for row in second["indices"])


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

    first = routes.get_market_overview()
    assert "commodities" not in first
    assert all(row["status"] == "LIVE" for row in first["indices"])

    with routes._OVERVIEW_QUOTE_CACHE_LOCK:
        for key, (refreshed_at, quotes) in tuple(routes._OVERVIEW_QUOTE_CACHE.items()):
            routes._OVERVIEW_QUOTE_CACHE[key] = (
                time.monotonic() - routes._OVERVIEW_QUOTE_CACHE_TTL_SECONDS - 1.0,
                quotes,
            )
    routes.get_market_overview.snapshot_cache.clear()
    client.fail = True

    second = routes.get_market_overview()
    assert all(row["status"] == "STALE" for row in second["indices"])
    assert any("stale quote snapshot used" in item["error"] for item in second["errors"])
    assert all(
        row["quote_cache_age_seconds"] > routes._OVERVIEW_QUOTE_CACHE_TTL_SECONDS
        for row in second["indices"]
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
    assert len(calls) == 2
    assert "commodities" not in first
    assert all(row["status"] == "NO_QUOTE" for row in first["indices"])

    routes.get_market_overview.snapshot_cache.clear()
    second = routes.get_market_overview()

    # Recent failures are negatively cached briefly so a 5-second browser poll
    # cannot hammer a broker endpoint that is already unavailable/rate-limited.
    assert len(calls) == 2
    assert all(row["status"] == "NO_QUOTE" for row in second["indices"])



def test_live_health_reuses_persisted_summary_without_staling_runtime_feed(monkeypatch):
    from datetime import datetime
    from app.core.config import settings

    summary_reads = []
    runtime_reads = []

    class FakeCatalog:
        @staticmethod
        def read_source_summary(path, *, source, timeframe):
            summary_reads.append((path, source, timeframe))
            return {
                "record_count": 12 if source == "angelone-live-1s" else 4,
                "instruments": 3 if source == "angelone-live-1s" else 2,
                "latest_timestamp_ns": None,
            }

    def runtime_snapshot():
        runtime_reads.append(1)
        return None

    monkeypatch.setattr(settings, "BACKTEST_DATA_DB", "/tmp/live-health-test.sqlite3")
    monkeypatch.setattr(
        "app.market_data.daily_shard_catalog.DailyMarketDataShardCatalog",
        FakeCatalog,
    )
    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        runtime_snapshot,
    )
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)

    first = routes.get_live_data_health()
    routes.get_live_data_health.snapshot_cache.clear()
    second = routes.get_live_data_health()

    # The expensive shard enumeration is shared for 60 seconds, but runtime
    # feed state is still sampled on every new /live-health snapshot.
    assert len(summary_reads) == 2
    assert len(runtime_reads) == 2
    assert first["records"] == second["records"] == 12
    assert second["persisted_summary"]["status"] == "FRESH"
    assert second["runtime_feed"]["status"] == "NO_DATA"
    assert second["feed_status"] == "NO_DATA"


def test_live_health_keeps_runtime_feed_available_when_persisted_shards_fail(monkeypatch):
    from app.core.config import settings

    class FailedReadOnlyCatalog:
        @staticmethod
        def read_source_summary(path, *, source, timeframe):
            raise OSError("sqlite shard is temporarily unavailable")

    runtime_reads = []

    def runtime_snapshot():
        runtime_reads.append(1)
        return None

    monkeypatch.setattr(settings, "BACKTEST_DATA_DB", "/tmp/unavailable-live-health.sqlite3")
    monkeypatch.setattr(
        "app.market_data.daily_shard_catalog.DailyMarketDataShardCatalog",
        FailedReadOnlyCatalog,
    )
    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        runtime_snapshot,
    )
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)

    result = routes.get_live_data_health()

    assert result["status"] == "success"
    assert result["persisted"] is None
    assert result["records"] is None
    assert result["persisted_summary"]["status"] == "UNAVAILABLE"
    assert result["runtime_feed"]["status"] == "NO_DATA"
    assert len(runtime_reads) == 1
