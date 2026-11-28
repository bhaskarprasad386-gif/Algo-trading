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



def test_shared_runner_health_lite_forwards_readiness_and_option_coverage(monkeypatch):
    snapshot = {
        "runner": "shared-index-market-data",
        "enabled": True,
        "core_market_data_ready": True,
        "live_data_ready": False,
        "readiness_reason": "OPTION_ACTIVITY_BELOW_POLICY",
        "option_quote_coverage": {
            "fresh": 36, "total": 124, "stale": 88,
            "complete": False, "percentage": 29.0,
        },
        "readiness": {
            "core_market_data_ready": True,
            "live_data_ready": False,
            "readiness_reason": "OPTION_ACTIVITY_BELOW_POLICY",
        },
    }
    monkeypatch.setattr(routes, "_shared_index_runner_health_provider", lambda: snapshot)

    result = routes.get_shared_index_runner_health_lite()

    assert result["core_market_data_ready"] is True
    assert result["live_data_ready"] is False
    assert result["readiness_reason"] == "OPTION_ACTIVITY_BELOW_POLICY"
    assert result["option_quote_coverage"]["fresh"] == 36
    assert result["option_quote_coverage"]["stale"] == 88


def test_shared_runner_health_lite_fails_closed_when_provider_missing(monkeypatch):
    monkeypatch.setattr(routes, "_shared_index_runner_health_provider", None)

    result = routes.get_shared_index_runner_health_lite()

    assert result["state"] == "NOT_INITIALIZED"
    assert result["enabled"] is False
    assert "live_data_ready" not in result


def test_shared_runner_health_lite_fails_closed_when_provider_raises(monkeypatch):
    def failed_snapshot():
        raise RuntimeError("health snapshot failed")

    monkeypatch.setattr(routes, "_shared_index_runner_health_provider", failed_snapshot)

    result = routes.get_shared_index_runner_health_lite()

    assert result["state"] == "UNAVAILABLE"
    assert "live_data_ready" not in result


def test_common_feed_health_reports_only_shared_runner_owner(monkeypatch):
    from app.core.config import settings

    feed_reads = []

    def feed_snapshot():
        feed_reads.append(1)
        return {"snapshot_kind": "health-lite", "ticks_received": 42}

    monkeypatch.setattr(
        "app.market_data.common_strategy_feed.shared_common_manager_snapshot",
        feed_snapshot,
    )
    monkeypatch.setattr(settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", False)

    first = routes.get_common_feed_health()
    second = routes.get_common_feed_health()

    assert first["feed"]["snapshot_kind"] == "health-lite"
    assert second["owner"] == "SharedIndexMarketDataRunner"
    assert second["universe"] == ["NIFTY", "BANKNIFTY"]
    assert "cash_future_runner" not in second
    assert len(feed_reads) == 1


def test_market_overview_uses_only_shared_runner_nifty_banknifty_quotes(monkeypatch):
    import time

    now_ns = time.time_ns()
    snapshot = {
        "enabled": True,
        "latest_spots": {
            "NIFTY": {"price": 24000.0, "timestamp_ns": now_ns, "age_seconds": 0.1},
            "BANKNIFTY": {"price": 52000.0, "timestamp_ns": now_ns, "age_seconds": 0.2},
        },
        "records": [
            {
                "underlying": "NIFTY", "instrument_type": "index", "ltp": 24000.0,
                "close": 23800.0, "open": 23900.0, "high": 24100.0, "low": 23750.0,
                "bid": 23999.5, "ask": 24000.5, "fresh": True, "feed_age_seconds": 0.1,
            },
            {
                "underlying": "BANKNIFTY", "instrument_type": "index", "ltp": 52000.0,
                "close": 51800.0, "open": 51900.0, "high": 52200.0, "low": 51700.0,
                "bid": 51999.0, "ask": 52001.0, "fresh": True, "feed_age_seconds": 0.2,
            },
        ],
    }
    monkeypatch.setattr(routes, "_shared_index_runner_snapshot_provider", lambda: snapshot)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: True)
    monkeypatch.setattr(
        "app.market_data.client.MarketDataClient",
        lambda: (_ for _ in ()).throw(AssertionError("overview must not call broker REST")),
    )

    result = routes.get_market_overview()

    assert result["status"] == "success"
    assert result["mode"] == "shared-runner-only"
    assert {row["symbol"] for row in result["indices"]} == {"NIFTY", "BANKNIFTY"}
    assert result["commodities"] == []
    assert result["mcx_market_session"] == "DISABLED"
    assert all(row["status"] == "LIVE" for row in result["indices"])
    assert result["indices"][0]["change_percent"] is not None
    assert result["errors"] == []


def test_market_overview_fails_closed_without_shared_runner_snapshot(monkeypatch):
    monkeypatch.setattr(routes, "_shared_index_runner_snapshot_provider", None)
    monkeypatch.setattr(routes, "_nse_market_is_open", lambda now: False)
    result = routes.get_market_overview()

    assert result["status"] == "success"
    assert result["mode"] == "shared-runner-only"
    assert {row["symbol"] for row in result["indices"]} == {"NIFTY", "BANKNIFTY"}
    assert all(row["status"] == "NO_DATA" for row in result["indices"])
    assert result["commodities"] == []
    assert result["errors"][0]["error"] == "runner_snapshot_unavailable"


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
