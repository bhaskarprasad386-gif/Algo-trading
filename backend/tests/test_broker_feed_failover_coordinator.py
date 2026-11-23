from app.market_data.broker_feed_failover_coordinator import MarketDataFailoverCoordinator
from app.market_data.broker_feed_failover import MarketDataFailoverPolicy


def policy():
    return MarketDataFailoverPolicy(
        angel_tick_fresh_seconds=1.0,
        shoonya_tick_fresh_seconds=1.0,
        angel_failover_stale_seconds=3.0,
        angel_failback_stability_seconds=5.0,
        minimum_shoonya_dwell_seconds=4.0,
    )


def tick(key, stamp, ltp):
    return {"instrument_key": key, "timestamp_ns": stamp, "ltp": ltp}


def test_coordinator_forwards_only_selected_provider_and_counts_suppressed():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda provider, record: received.append((provider, record)))
    assert c.observe("angel_one", tick("NIFTY", 10, 100)) is True
    assert c.observe("shoonya", tick("NIFTY", 11, 101)) is False
    assert c.evaluate(
        failed_angel_sockets=3, angel_tick_age_seconds=4.0,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100.0,
    ).active_provider == "shoonya"
    assert c.observe("shoonya", tick("NIFTY", 12, 102)) is True
    assert [provider for provider, _ in received] == ["angel_one", "shoonya"]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 2
    assert snap["suppressed_ticks"] == 1


def test_coordinator_rejects_out_of_order_and_invalid_ticks():
    received = []
    c = MarketDataFailoverCoordinator(on_record=lambda provider, record: received.append(record))
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is True
    assert c.observe("angel_one", tick("NIFTY", 99, 99)) is False
    assert c.observe("angel_one", {"instrument_key": "NIFTY", "timestamp_ns": -1}) is False
    assert len(received) == 1
    snap = c.snapshot()
    assert snap["suppressed_ticks"] == 1
    assert snap["invalid_records"] == 1


def test_coordinator_can_fail_closed_when_policy_has_no_valid_provider():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda provider, record: received.append(record))
    decision = c.evaluate(
        failed_angel_sockets=3, angel_tick_age_seconds=5.0,
        shoonya_connected=False, shoonya_tick_age_seconds=None, now=50.0,
    )
    assert decision.active_provider == "none"
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is False
    assert not received
