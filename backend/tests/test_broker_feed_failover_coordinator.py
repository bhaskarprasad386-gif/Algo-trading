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


def test_callback_failure_is_counted_and_does_not_claim_forwarding():
    def broken_callback(_provider, _record):
        raise RuntimeError("consumer unavailable")

    c = MarketDataFailoverCoordinator(on_record=broken_callback)
    assert c.observe("angel_one", tick("NIFTY", 200, 200)) is False
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 0
    assert snap["callback_errors"] == 1
    assert snap["suppressed_ticks"] == 0


def test_provider_evaluation_waits_for_inflight_selected_callback():
    from threading import Event, Thread


    entered = Event()
    release = Event()
    forwarded = []

    def callback(provider, record):
        entered.set()
        assert release.wait(timeout=2)
        forwarded.append((provider, record["timestamp_ns"]))


    c = MarketDataFailoverCoordinator(policy=policy(), on_record=callback)
    observer = Thread(target=lambda: c.observe("angel_one", tick("NIFTY", 300, 300)))
    observer.start()
    assert entered.wait(timeout=2)
    evaluated = Event()

    def evaluate_backup():
        c.evaluate(
            failed_angel_sockets=3, angel_tick_age_seconds=4.0,
            shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100.0,
        )
        evaluated.set()

    evaluator = Thread(target=evaluate_backup)
    evaluator.start()
    assert not evaluated.wait(timeout=0.05)
    release.set()
    observer.join(timeout=2)
    evaluator.join(timeout=2)
    assert not observer.is_alive()
    assert not evaluator.is_alive()
    assert forwarded == [("angel_one", 300)]
    assert c.snapshot()["active_provider"] == "shoonya"



def test_callback_failure_does_not_block_later_successful_delivery():
    received = []
    calls = 0


    def callback(provider, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary consumer failure")
        received.append((provider, record["timestamp_ns"]))


    c = MarketDataFailoverCoordinator(on_record=callback)
    assert c.observe("angel_one", tick("NIFTY", 400, 400)) is False
    assert c.observe("angel_one", tick("NIFTY", 401, 401)) is True
    snap = c.snapshot()
    assert received == [("angel_one", 401)]
    assert snap["callback_errors"] == 1
    assert snap["forwarded_ticks"] == 1
    assert snap["suppressed_ticks"] == 0



def test_consumer_mutations_do_not_change_input_or_cached_tick_payload():
    original = {"instrument_key": "NIFTY", "timestamp_ns": 500, "metadata": {"source": "feed"}}


    def callback(_provider, record):
        record["metadata"]["source"] = "consumer-mutated"


    c = MarketDataFailoverCoordinator(on_record=callback)
    assert c.observe("angel_one", original) is True
    assert original["metadata"]["source"] == "feed"
    assert c.snapshot()["latest_record_count"] == 1



def test_late_primary_ticks_are_suppressed_after_failover_and_backup_keeps_order():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda p, r: received.append((p, r["timestamp_ns"])))
    assert c.observe("angel_one", tick("NIFTY", 10, 100)) is True
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    assert c.observe("angel_one", tick("NIFTY", 11, 101)) is False
    assert c.observe("shoonya", tick("NIFTY", 20, 200)) is True
    assert c.observe("shoonya", tick("NIFTY", 19, 199)) is False
    assert received == [("angel_one", 10), ("shoonya", 20)]


def test_no_valid_provider_suppresses_both_sources_until_policy_selects_one():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda p, r: received.append(p))
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
               shoonya_connected=False, shoonya_tick_age_seconds=None, now=10)
    assert c.observe("angel_one", tick("NIFTY", 1, 100)) is False
    assert c.observe("shoonya", tick("NIFTY", 1, 100)) is False
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    assert c.observe("shoonya", tick("NIFTY", 2, 101)) is True
    assert received == ["shoonya"]


def test_timestamp_order_is_tracked_per_provider_and_per_instrument():
    received = []
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: received.append((p, r["instrument_key"], r["timestamp_ns"])))
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is True
    assert c.observe("angel_one", tick("BANKNIFTY", 50, 50)) is True
    assert c.observe("angel_one", tick("NIFTY", 99, 99)) is False
    assert c.observe("angel_one", tick("BANKNIFTY", 51, 51)) is True
    assert received == [("angel_one", "NIFTY", 100), ("angel_one", "BANKNIFTY", 50), ("angel_one", "BANKNIFTY", 51)]


def test_callback_reentrant_health_switch_applies_only_to_subsequent_ticks():
    received = []
    holder = {}
    switched = []

    def callback(provider, record):
        received.append((provider, record["timestamp_ns"]))
        if not switched:
            switched.append(True)
            holder["coordinator"].evaluate(
                failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10,
            )

    c = MarketDataFailoverCoordinator(policy=policy(), on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("NIFTY", 1, 100)) is True
    assert c.snapshot()["active_provider"] == "shoonya"
    assert c.observe("angel_one", tick("NIFTY", 2, 101)) is False
    assert c.observe("shoonya", tick("NIFTY", 3, 102)) is True
    assert received == [("angel_one", 1), ("shoonya", 3)]
