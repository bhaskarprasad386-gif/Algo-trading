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


def test_offmarket_butterfly_condor_shadow_rejects_stale_quotes_without_side_effects():
    from copy import deepcopy

    from app.scanner.butterfly_condor_shadow import evaluate_butterfly_condor_snapshot

    captured_ns = 1_800_000_000_000_000_000
    snapshot = {
        "quotes": {
            "captured_at_ns": captured_ns,
            "groups": [{
                "underlying": "NIFTY",
                "expiry": 1_800_000_000,
                "expiry_type": "WEEKLY",
                "atm_strike": 25000,
                "data": [{
                    "instrument_class": "INDEX",
                    "option_type": "CE",
                    "strike": 25000,
                    "bid": 100.0,
                    "ask": 101.0,
                    "bid_qty": 50,
                    "ask_qty": 50,
                    "lot_size": 75,
                    "timestamp_ns": captured_ns - 6_000_000_000,
                    "freshness": "STALE",
                }],
            }],
        },
        "depth": {"records": []},
    }
    original = deepcopy(snapshot)

    result = evaluate_butterfly_condor_snapshot(snapshot)

    assert snapshot == original
    assert result["source"] == "box-spread-shared-snapshot"
    assert result["read_only"] is True
    assert result["execution_mode"] == "SHADOW_ONLY"
    assert result["order_routing_enabled"] is False
    assert result["result_count"] == 0
    assert result["results"] == []
    assert result["rejected_counts"]["STALE_QUOTE"] == 1
    assert result["rejected_counts"]["INVALID_QUOTE"] == 0
    assert result["rejected_counts"]["STALE_OR_INVALID_QUOTE"] == 1
    assert result["eligibility"]["minimum_gross_expiry_profit_per_unit_exclusive"] == 5.0


def test_offmarket_butterfly_condor_shadow_empty_snapshot_is_safe_and_read_only():
    from app.scanner.butterfly_condor_shadow import evaluate_butterfly_condor_snapshot

    result = evaluate_butterfly_condor_snapshot({
        "quotes": {"captured_at_ns": 1_800_000_000_000_000_000, "groups": []},
        "depth": {"records": []},
    })

    assert result["schema_version"] == 1
    assert result["read_only"] is True
    assert result["execution_mode"] == "SHADOW_ONLY"
    assert result["order_routing_enabled"] is False
    assert result["result_count"] == 0
    assert result["results"] == []
