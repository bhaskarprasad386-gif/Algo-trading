from app.market_data.common_strategy_feed import CommonStrategyMarketFeed, replay_cached_snapshots
from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner, _box_underlying_health_snapshot
from app.market_data.shared_cache import get_shared_market_data_cache


def test_offmarket_replay_accepts_only_snapshots_inside_freshness_and_future_skew(monkeypatch):
    import app.market_data.common_strategy_feed as feed_module

    now_ns = 1_800_000_000_000_000_000
    monkeypatch.setattr(feed_module, "time_ns", lambda: now_ns)
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptors = [
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-fresh", symbol="FRESH-EQ",
            instrument_type="equity", segment="NSE",
        ),
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-stale", symbol="STALE-EQ",
            instrument_type="equity", segment="NSE",
        ),
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-future", symbol="FUTURE-EQ",
            instrument_type="equity", segment="NSE",
        ),
    ]
    try:
        cache.put(descriptors[0].key.value, {"timestamp_ns": now_ns + 500_000_000, "ltp": 101.0})
        cache.put(descriptors[1].key.value, {"timestamp_ns": now_ns - 6_000_000_000, "ltp": 99.0})
        cache.put(descriptors[2].key.value, {"timestamp_ns": now_ns + 1_500_000_000, "ltp": 102.0})

        received = []
        count = replay_cached_snapshots(descriptors, received.append)

        assert count == 1
        assert len(received) == 1
        assert received[0].symbol == "FRESH-EQ"
        assert received[0].ltp == 101.0
    finally:
        cache.clear()


def test_offmarket_replay_rejects_invalid_freshness_bounds():
    import pytest

    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE", token="offmarket-invalid-bounds", symbol="INVALID-EQ",
        instrument_type="equity", segment="NSE",
    )
    with pytest.raises(ValueError, match="freshness bounds"):
        replay_cached_snapshots([descriptor], lambda _record: None, max_age_seconds=0)
    with pytest.raises(ValueError, match="freshness bounds"):
        replay_cached_snapshots([descriptor], lambda _record: None, max_future_skew_seconds=-0.1)


def test_offmarket_box_readiness_fails_closed_without_fresh_underlying_ticks():
    snapshot = {
        "underlying_feed": {
            "symbols": {
                "NIFTY": {"state": "STALE"},
                "SENSEX": {"state": "WAITING_FOR_TICK"},
            }
        },
        "option_recorder": {"state": "not_started"},
        "depth_shadow": {"enabled": True, "running": False, "readiness": []},
    }

    result = LiveBoxSpreadRunner.staged_session_readiness(
        snapshot,
        {("NIFTY", "17NOV2026"), ("SENSEX", "19NOV2026")},
    )

    assert result["ready"] is False
    assert result["state"] == "WAITING_FOR_FEEDS"
    assert result["missing_underlying_symbols"] == ["NIFTY", "SENSEX"]
    assert "underlying_ticks_not_fresh:NIFTY,SENSEX" in result["reasons"]


def test_offmarket_underlying_snapshot_preserves_stale_and_waiting_states():
    result = _box_underlying_health_snapshot({
        "state": "LIVE",
        "symbols": {
            "NIFTY": {"state": "STALE"},
            "SENSEX": {"state": "WAITING_FOR_TICK"},
        },
    })

    assert result["state"] == "STALE"
    assert result["fresh_symbol_count"] == 0
    assert result["stale_symbol_count"] == 1
    assert result["waiting_symbol_count"] == 1
