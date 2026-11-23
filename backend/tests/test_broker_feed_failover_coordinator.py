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
    assert c._latest[("angel_one", "NIFTY")]["metadata"]["source"] == "feed"
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


def test_cross_provider_duplicate_timestamp_is_suppressed_after_switch():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda p, r: received.append((p, r["timestamp_ns"])))
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is True
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
              shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # Backup's overlapping replay of the last primary timestamp must not duplicate.
    assert c.observe("shoonya", tick("NIFTY", 100, 100)) is False
    assert c.observe("shoonya", tick("NIFTY", 101, 101)) is True
    assert received == [("angel_one", 100), ("shoonya", 101)]


def test_backup_stale_gap_suppresses_ticks_then_fresh_backup_recovers():
    received = []
    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda p, r: received.append((p, r["timestamp_ns"])))
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
              shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    assert c.observe("shoonya", tick("NIFTY", 10, 100)) is True
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
              shoonya_connected=False, shoonya_tick_age_seconds=None, now=11)
    assert c.snapshot()["active_provider"] == "none"
    assert c.observe("shoonya", tick("NIFTY", 11, 101)) is False
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
              shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=12)
    assert c.observe("shoonya", tick("NIFTY", 12, 102)) is True
    assert received == [("shoonya", 10), ("shoonya", 12)]



def test_failed_callback_does_not_advance_cross_provider_watermark():
    delivered = []
    first = True

    def callback(provider, record):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("transient consumer failure")
        delivered.append((provider, record["timestamp_ns"]))

    c = MarketDataFailoverCoordinator(policy=policy(), on_record=callback)
    assert c.observe("angel_one", tick("NIFTY", 500, 100)) is False
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # The backup's same timestamp is eligible because the primary callback failed.
    assert c.observe("shoonya", tick("NIFTY", 500, 101)) is True
    assert delivered == [("shoonya", 500)]
    assert c.snapshot()["callback_errors"] == 1
    assert c.snapshot()["forwarded_ticks"] == 1



def test_callback_exception_cannot_mutate_cached_payload_on_failure():
    original = {"instrument_key": "BANKNIFTY", "timestamp_ns": 600,
              "metadata": {"origin": "canonical"}}


    def callback(_provider, record):
        record["metadata"]["origin"] = "mutated-before-raise"
        raise RuntimeError("consumer failed")


    c = MarketDataFailoverCoordinator(on_record=callback)
    assert c.observe("angel_one", original) is False
    assert original["metadata"]["origin"] == "canonical"
    assert c._latest[("angel_one", "BANKNIFTY")]["metadata"]["origin"] == "canonical"
    assert c.snapshot()["callback_errors"] == 1



def test_concurrent_same_instrument_observations_forward_in_timestamp_order():
    from threading import Barrier, Thread

    forwarded = []
    barrier = Barrier(3)
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: forwarded.append(r["timestamp_ns"]))

    def send(stamp):
        barrier.wait(timeout=2)
        c.observe("angel_one", tick("NIFTY", stamp, stamp))

    low = Thread(target=send, args=(700,))
    high = Thread(target=send, args=(701,))
    low.start()
    high.start()
    barrier.wait(timeout=2)
    low.join(timeout=2)
    high.join(timeout=2)
    assert not low.is_alive() and not high.is_alive()
    # Lock serialization means the winning order is monotonic; if 701 wins,
    # 700 is suppressed rather than regressing downstream state.
    assert forwarded in ([700, 701], [701])
    assert all(a < b for a, b in zip(forwarded, forwarded[1:]))
    assert c.snapshot()["forwarded_ticks"] == len(forwarded)



def test_reentrant_snapshot_inside_callback_reports_pre_delivery_counter():
    snapshots = []
    holder = {}


    def callback(_provider, _record):
        snapshots.append(holder["c"].snapshot())


    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["c"] = c
    assert c.observe("angel_one", tick("NIFTY", 800, 100)) is True
    assert snapshots[0]["forwarded_ticks"] == 0
    assert c.snapshot()["forwarded_ticks"] == 1
    assert snapshots[0]["latest_record_count"] == 1


def test_normalized_instrument_key_aliases_share_ordering_watermarks():
    received = []
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: received.append(r["timestamp_ns"]))
    assert c.observe("angel_one", {"key": " NIFTY ", "timestamp_ns": 900}) is True
    assert c.observe("angel_one", {"instrument_key": "NIFTY", "timestamp_ns": 899}) is False
    assert c.observe("angel_one", {"instrument_key": "NIFTY", "timestamp_ns": 901}) is True
    assert received == [900, 901]
    assert c.snapshot()["forwarded_ticks"] == 2


def test_instrument_key_whitespace_only_record_is_rejected():
    c = MarketDataFailoverCoordinator(on_record=lambda _provider, _record: None)
    assert c.observe("angel_one", {"instrument_key": "   ", "timestamp_ns": 1000}) is False
    snap = c.snapshot()
    assert snap["invalid_records"] == 1
    assert snap["latest_record_count"] == 0


def test_policy_failover_threshold_boundary_is_inclusive():
    p = policy()
    grace = p.update(failed_angel_sockets=0, angel_tick_age_seconds=2.99,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1)
    assert grace.active_provider == "none"
    assert grace.mode == "DEGRADED_WAIT"
    switch = p.update(failed_angel_sockets=0, angel_tick_age_seconds=3.0,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=2)
    assert switch.active_provider == "shoonya"
    assert switch.reason == "angel_feed_stale"


def test_policy_failback_stability_timer_resets_after_health_interruption():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # Angel's recovery begins but is interrupted before stability threshold.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=14)
    # A new stability window starts only after uninterrupted recovery.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=15)
    still_backup = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=19.9)
    assert still_backup.active_provider == "shoonya"
    failback = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=20)
    assert failback.active_provider == "angel_one"
    assert failback.reason == "angel_stable_failback"


def test_policy_rejects_out_of_order_health_update_without_state_change():
    p = policy()
    first = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=30)
    try:
        p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=29)
    except ValueError as exc:
        assert "out-of-order" in str(exc)
    else:
        raise AssertionError("out-of-order health update must be rejected")
    assert p.snapshot().active_provider == first.active_provider
    assert p.snapshot().mode == first.mode


def test_backup_dwell_boundary_blocks_failback_until_exact_elapsed_time():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=101)
    before_dwell = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=103.99)
    assert before_dwell.active_provider == "shoonya"
    at_boundary = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=106)
    assert at_boundary.active_provider == "angel_one"


def test_invalid_health_arguments_leave_policy_state_unchanged():
    p = policy()
    before = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=5)
    invalid_inputs = [
        dict(failed_angel_sockets=True, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=6),
        dict(failed_angel_sockets=0, angel_tick_age_seconds=float("nan"),
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=6),
        dict(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=1, shoonya_tick_age_seconds=0.2, now=6),
    ]
    for kwargs in invalid_inputs:
        try:
            p.update(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid health inputs must be rejected")
        after = p.snapshot()
        assert after.active_provider == before.active_provider
        assert after.mode == before.mode
        assert after.reason == before.reason
        assert after.switched_at == before.switched_at


def test_backup_dropout_fails_closed_until_primary_stability_and_dwell_allow_failback():
    p = policy()
    # Start on backup after Angel's feed becomes stale.
    switched = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100)
    assert switched.active_provider == "shoonya"
    # Primary has recovered, but backup drops before hysteresis is satisfied.
    dropout = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=False, shoonya_tick_age_seconds=None, now=101)
    assert dropout.active_provider == "none"
    assert dropout.mode == "NO_VALID_FEED"
    assert dropout.reason == "shoonya_feed_stale"
    # The next health evaluation may select Angel once it is fresh; no stale backup is selected.
    recovered = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=False, shoonya_tick_age_seconds=None, now=102)
    assert recovered.active_provider == "angel_one"
    assert recovered.reason == "angel_recovered"


def test_backup_dropout_and_angel_recovery_then_backup_recovery_are_deterministic():
    p = policy()
    # Switch to Shoonya when Angel becomes stale.
    backup = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    assert backup.active_provider == "shoonya"
    # Shoonya drops, Angel is fresh: fail closed immediately, not on stale backup.
    outage = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                     shoonya_connected=False, shoonya_tick_age_seconds=None, now=11)
    assert outage.active_provider == "none"
    assert outage.reason == "shoonya_feed_stale"
    # On the following evaluation, fresh Angel can recover authority.
    primary = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=False, shoonya_tick_age_seconds=None, now=12)
    assert primary.active_provider == "angel_one"
    assert primary.reason == "angel_recovered"
    # If Angel then degrades again, fresh Shoonya is selected without stale history.
    next_backup = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                          shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=13)
    assert next_backup.active_provider == "shoonya"
    assert next_backup.reason == "two_or_more_angel_sockets_failed_and_feed_unhealthy"


def test_stale_tick_replay_is_suppressed_after_backup_to_primary_handoff():
    delivered = []
    c = MarketDataFailoverCoordinator(policy=policy(),
        on_record=lambda provider, record: delivered.append((provider, record["timestamp_ns"])))
    assert c.observe("angel_one", tick("NIFTY", 10, 100)) is True
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    assert c.observe("shoonya", tick("NIFTY", 20, 200)) is True
    # Angel recovers continuously; dwell and stability complete together at t=20.
    c.evaluate(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    recovered = c.evaluate(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
               shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=20)
    assert recovered.active_provider == "angel_one"
    # Older Angel replay is rejected by the cross-provider forwarding watermark.
    assert c.observe("angel_one", tick("NIFTY", 19, 190)) is False
    assert c.observe("angel_one", tick("NIFTY", 21, 210)) is True
    assert delivered == [("angel_one", 10), ("shoonya", 20), ("angel_one", 21)]
    assert c.snapshot()["suppressed_ticks"] == 1


def test_rapid_health_oscillation_never_bypasses_continuous_failback_stability():
    p = policy()
    assert p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=0).active_provider == "shoonya"
    # Repeated brief healthy windows are interrupted and must reset the timer.
    for healthy_at, interrupted_at in ((1, 3), (4, 6), (7, 9)):
        assert p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
            shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=healthy_at).active_provider == "shoonya"
        assert p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
            shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=interrupted_at).active_provider == "shoonya"
    # Only the uninterrupted healthy period starting at 10 can satisfy 5s.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    assert p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=14.99).active_provider == "shoonya"
    assert p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=15).active_provider == "angel_one"


def test_simultaneous_equal_time_health_updates_are_atomic_and_valid():
    from threading import Barrier, Thread

    p = policy()
    barrier = Barrier(3)
    results = []
    errors = []

    def update(angel_age, shoonya_connected, backup_age):
        barrier.wait(timeout=2)
        try:
            results.append(p.update(
                failed_angel_sockets=3 if angel_age >= 3 else 0,
                angel_tick_age_seconds=angel_age,
                shoonya_connected=shoonya_connected,
                shoonya_tick_age_seconds=backup_age,
                now=100,
            ))
        except Exception as exc:
            errors.append(exc)

    # Concurrent inputs at the same timestamp must serialize as whole decisions,
    # never produce a torn/invalid snapshot or corrupt internal state.
    a = Thread(target=update, args=(4.0, True, 0.2))
    b = Thread(target=update, args=(0.2, True, 0.2))
    a.start()
    b.start()
    barrier.wait(timeout=2)
    a.join(timeout=2)
    b.join(timeout=2)
    assert not a.is_alive() and not b.is_alive()
    assert errors == []
    assert len(results) == 2
    assert all(r.active_provider in ("angel_one", "shoonya", "none") for r in results)
    final = p.snapshot()
    assert final.active_provider in ("angel_one", "shoonya", "none")
    assert final.mode in ("PRIMARY", "BACKUP", "NO_VALID_FEED", "DEGRADED_WAIT")


def test_coordinator_concurrent_same_time_switches_keep_snapshot_consistent():
    from threading import Barrier, Thread

    c = MarketDataFailoverCoordinator(policy=policy(), on_record=lambda _p, _r: None)
    barrier = Barrier(3)
    exceptions = []

    def evaluate(kwargs):
        barrier.wait(timeout=2)
        try:
            c.evaluate(**kwargs)
        except Exception as exc:
            exceptions.append(exc)

    backup_health = dict(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=50)
    primary_health = dict(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=50)
    threads = [Thread(target=evaluate, args=(backup_health,)),
               Thread(target=evaluate, args=(primary_health,))]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert exceptions == []
    snap = c.snapshot()
    policy_snap = c.policy.snapshot()
    assert snap["active_provider"] == policy_snap.active_provider
    assert snap["mode"] == policy_snap.mode
    assert snap["reason"] == policy_snap.reason


def test_policy_rejects_delayed_health_event_while_on_backup_without_mutation():
    p = policy()
    backup = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100)
    assert backup.active_provider == "shoonya"
    # Delayed healthy-primary event with older monotonic evaluation time must not
    # reset failback stability or change backup authority.
    try:
        p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
            shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=99.5)
    except ValueError as exc:
        assert "out-of-order" in str(exc)
    else:
        raise AssertionError("delayed health event must be rejected")
    after = p.snapshot()
    assert after.active_provider == backup.active_provider
    assert after.mode == backup.mode
    assert after.reason == backup.reason
    assert after.switched_at == backup.switched_at
    # A properly ordered health event begins the stability timer normally.
    recovered = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=101)
    assert recovered.active_provider == "shoonya"
    assert recovered.reason == "shoonya_active_waiting_for_angel_stability"


def test_concurrent_failback_and_backup_tick_handoff_delivers_timestamp_once():
    from threading import Barrier, Thread

    delivered = []
    c = MarketDataFailoverCoordinator(policy=policy(),
        on_record=lambda provider, record: delivered.append((provider, record["timestamp_ns"])))
    c.evaluate(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    c.evaluate(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    # At t=15 both dwell and Angel stability are satisfied.
    barrier = Barrier(3)
    errors = []

    def backup_tick():
        barrier.wait(timeout=2)
        try:
            c.observe("shoonya", tick("NIFTY", 1000, 100))
        except Exception as exc:
            errors.append(exc)

    def failback():
        barrier.wait(timeout=2)
        try:
            c.evaluate(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=15)
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=backup_tick), Thread(target=failback)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    # Depending on lock acquisition order, the backup tick is delivered before
    # failback or rejected after the switch; the timestamp is never duplicated.
    assert delivered in ([("shoonya", 1000)], [])
    assert c.snapshot()["forwarded_ticks"] == len(delivered)

def test_callback_failure_allows_same_provider_same_timestamp_retry():
    attempts = []
    def callback(provider, record):
        attempts.append(record["timestamp_ns"])
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
    c = MarketDataFailoverCoordinator(on_record=callback)
    same_tick = tick("NIFTY", 1234, 100)
    assert c.observe("angel_one", same_tick) is False
    assert c.observe("angel_one", same_tick) is True
    assert attempts == [1234, 1234]
    snap = c.snapshot()
    assert snap["callback_errors"] == 1
    assert snap["forwarded_ticks"] == 1


def test_forget_instrument_clears_all_provider_cache_and_watermarks():
    received = []
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: received.append((p, r["timestamp_ns"])))
    assert c.observe("angel_one", tick(" NIFTY ", 100, 100)) is True
    assert c.observe("shoonya", tick("NIFTY", 101, 101)) is False
    assert c.forget_instrument("NIFTY") is True
    assert c.snapshot()["latest_record_count"] == 0
    assert c.forget_instrument("NIFTY") is False
    # After universe re-add, older timestamps may be accepted as a fresh epoch.
    assert c.observe("angel_one", tick("NIFTY", 102, 99)) is True
    assert received == [("angel_one", 100), ("angel_one", 102)]

def test_prune_instruments_removes_retired_provider_state_and_is_idempotent():
    delivered = []
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: delivered.append((p, r["instrument_key"])))
    assert c.observe("angel_one", tick("NIFTY", 10, 100)) is True
    assert c.observe("angel_one", tick("BANKNIFTY", 20, 200)) is True
    assert c.observe("shoonya", tick("BANKNIFTY", 21, 201)) is False
    assert c.prune_instruments({"NIFTY"}) == 1
    assert c.snapshot()["latest_record_count"] == 1
    assert c.prune_instruments({"NIFTY"}) == 0
    # Retired instrument may reappear with a new timestamp epoch.
    assert c.observe("angel_one", tick("BANKNIFTY", 1, 199)) is True
    assert delivered[-1] == ("angel_one", "BANKNIFTY")


def test_prune_instruments_rejects_string_argument():
    import pytest
    c = MarketDataFailoverCoordinator(on_record=lambda _provider, _record: None)
    with pytest.raises(TypeError, match="not a string"):
        c.prune_instruments("NIFTY")


def test_reentrant_callback_cannot_deliver_same_tick_twice():
    calls = []
    holder = {}
    record = tick("NIFTY", 1500, 100)

    def callback(provider, payload):
        calls.append((provider, payload["timestamp_ns"]))
        # Re-enter with the exact same tick while the outer callback is active.
        assert holder["c"].observe(provider, record) is False


    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["c"] = c
    assert c.observe("angel_one", record) is True
    assert calls == [("angel_one", 1500)]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 1
    assert snap["suppressed_ticks"] == 1
    assert snap["callback_errors"] == 0


def test_outer_callback_failure_preserves_newer_reentrant_tick_watermarks():
    delivered = []
    holder = {}
    older = tick("NIFTY", 1500, 100)
    newer = tick("NIFTY", 1600, 101)

    def callback(provider, payload):
        delivered.append(payload["timestamp_ns"])
        if payload["timestamp_ns"] == 1500:
            assert holder["c"].observe(provider, newer) is True
            raise RuntimeError("outer callback failed after reentrant delivery")

    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["c"] = c
    assert c.observe("angel_one", older) is False
    # Outer failure must not roll back the newer tick delivered re-entrantly.
    assert c.observe("angel_one", tick("NIFTY", 1550, 100.5)) is False
    assert c.observe("angel_one", newer) is False
    assert delivered == [1500, 1600]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 1
    assert snap["callback_errors"] == 1
    assert snap["suppressed_ticks"] == 2

def test_reentrant_provider_switch_suppresses_backup_replay_of_inflight_primary_tick():
    delivered = []
    holder = {}
    same = tick("NIFTY", 4242, 100)

    def callback(provider, record):
        delivered.append((provider, record["timestamp_ns"]))
        if provider == "angel_one":
            holder["c"].evaluate(
                failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=42,
            )
            # Backup reconnect/replay inside the in-flight callback must not
            # duplicate the primary timestamp reserved by the outer delivery.
            assert holder["c"].observe("shoonya", same) is False

    c = MarketDataFailoverCoordinator(policy=policy(), on_record=callback)
    holder["c"] = c
    assert c.observe("angel_one", same) is True
    assert delivered == [("angel_one", 4242)]
    snap = c.snapshot()
    assert snap["active_provider"] == "shoonya"
    assert snap["forwarded_ticks"] == 1
    assert snap["suppressed_ticks"] == 1
    assert snap["callback_errors"] == 0


def test_uncloneable_canonical_payload_is_counted_invalid_without_crashing():
    class BrokenCopy:
        def __deepcopy__(self, _memo):
            raise RuntimeError("cannot clone payload")

    c = MarketDataFailoverCoordinator(on_record=lambda _p, _r: None)
    record = tick("NIFTY", 9999, 100)
    record["metadata"] = BrokenCopy()
    assert c.observe("angel_one", record) is False
    snap = c.snapshot()
    assert snap["invalid_records"] == 1
    assert snap["latest_record_count"] == 0
    assert snap["forwarded_ticks"] == 0
    assert snap["callback_errors"] == 0
