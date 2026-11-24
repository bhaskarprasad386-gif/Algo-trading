from app.market_data.broker_feed_failover_coordinator import MarketDataFailoverCoordinator


def tick(stamp):
    return {"instrument_key": "NIFTY", "timestamp_ns": stamp, "ltp": stamp}


def test_outer_callback_failure_cannot_rollback_reentrant_same_timestamp_after_retirement():
    delivered = []
    holder = {}
    calls = 0

    def callback(provider, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert holder["coordinator"].forget_instrument("NIFTY") is True
            assert holder["coordinator"].observe(provider, tick(record["timestamp_ns"])) is True
            raise RuntimeError("outer callback fails after reentrant delivery")
        delivered.append((provider, record["timestamp_ns"]))

    coordinator = MarketDataFailoverCoordinator(on_record=callback)
    holder["coordinator"] = coordinator

    assert coordinator.observe("angel_one", tick(1700)) is False
    # Nested same-timestamp delivery succeeded; outer failure must not clear its
    # committed replay watermark and permit a third duplicate delivery.
    assert delivered == [("angel_one", 1700)]
    assert coordinator.observe("angel_one", tick(1700)) is False
    snapshot = coordinator.snapshot()
    assert snapshot["forwarded_ticks"] == 1
    assert snapshot["callback_errors"] == 1
    assert snapshot["latest_record_count"] == 1
