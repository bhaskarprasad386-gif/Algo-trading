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

def test_reentrant_newer_tick_survives_outer_callback_failure_rollback():
    delivered = []
    holder = {}
    calls = 0

    def callback(provider, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Re-enter with a newer timestamp, then fail the outer delivery.
            assert holder["coordinator"].observe(
                provider, tick(record["instrument_key"], 101, 101)
            ) is True
            raise RuntimeError("outer callback failed after nested delivery")
        delivered.append((provider, record["timestamp_ns"]))

    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is False
    assert c.observe("angel_one", tick("NIFTY", 102, 102)) is True
    assert delivered == [("angel_one", 101), ("angel_one", 102)]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 2
    assert snap["callback_errors"] == 1
    assert snap["suppressed_ticks"] == 0


def test_forget_instrument_reentrant_callback_resets_watermarks_atomically():
    holder = {}
    callbacks = []

    def callback(provider, record):
        callbacks.append((provider, record["timestamp_ns"]))
        # Retirement is allowed from a consumer callback; the outer delivery
        # must not recreate the just-cleared ordering/cache state afterward.
        assert holder["coordinator"].forget_instrument(record["instrument_key"]) is True

    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("BANKNIFTY", 200, 200)) is True
    snap_after_first = c.snapshot()
    assert snap_after_first["latest_record_count"] == 0
    assert c.observe("angel_one", tick("BANKNIFTY", 200, 201)) is True
    assert callbacks == [("angel_one", 200), ("angel_one", 200)]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 2
    assert snap["callback_errors"] == 0
    assert snap["latest_record_count"] == 1

def test_reentrant_backup_delivery_watermark_survives_primary_callback_failure():
    delivered = []
    holder = {}
    calls = 0

    def callback(provider, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            holder["coordinator"].evaluate(
                failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10,
            )
            assert holder["coordinator"].observe(
                "shoonya", tick(record["instrument_key"], 101, 101)
            ) is True
            raise RuntimeError("primary callback failed after backup handoff")
        delivered.append((provider, record["timestamp_ns"]))

    c = MarketDataFailoverCoordinator(policy=policy(), on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("NIFTY", 100, 100)) is False
    assert c.observe("shoonya", tick("NIFTY", 102, 102)) is True
    assert delivered == [("shoonya", 101), ("shoonya", 102)]
    snap = c.snapshot()
    assert snap["active_provider"] == "shoonya"
    assert snap["forwarded_ticks"] == 2
    assert snap["callback_errors"] == 1
    assert snap["suppressed_ticks"] == 0

def test_reentrant_duplicate_timestamp_is_suppressed_before_outer_callback_commits():
    holder = {}
    nested_results = []
    callbacks = []

    def callback(provider, record):
        callbacks.append((provider, record["timestamp_ns"]))
        if len(callbacks) == 1:
            nested_results.append(holder["coordinator"].observe(provider, record))

    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("NIFTY", 300, 300)) is True
    assert nested_results == [False]
    assert callbacks == [("angel_one", 300)]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 1
    assert snap["suppressed_ticks"] == 1
    assert snap["callback_errors"] == 0


def test_prune_instrument_universe_reentrant_from_callback_clears_watermarks():
    holder = {}
    callbacks = []

    def callback(provider, record):
        callbacks.append((provider, record["instrument_key"], record["timestamp_ns"]))
        if len(callbacks) == 1:
            assert holder["coordinator"].prune_instruments([]) == 1

    c = MarketDataFailoverCoordinator(on_record=callback)
    holder["coordinator"] = c
    assert c.observe("angel_one", tick("NIFTY", 400, 400)) is True
    assert c.snapshot()["latest_record_count"] == 0
    # Universe retirement resets timestamp ordering, allowing a newly subscribed
    # instance of the same normalized key to start with its own clock.
    assert c.observe("angel_one", tick(" NIFTY ", 400, 401)) is True
    assert callbacks == [("angel_one", "NIFTY", 400), ("angel_one", " NIFTY ", 400)]
    snap = c.snapshot()
    assert snap["forwarded_ticks"] == 2
    assert snap["callback_errors"] == 0
    assert snap["latest_record_count"] == 0


def test_pruning_one_instrument_preserves_other_instrument_ordering_state():
    received = []
    c = MarketDataFailoverCoordinator(on_record=lambda p, r: received.append((r["instrument_key"], r["timestamp_ns"])))
    assert c.observe("angel_one", tick("NIFTY", 500, 100)) is True
    assert c.observe("angel_one", tick("BANKNIFTY", 700, 200)) is True
    assert c.prune_instruments(["BANKNIFTY"]) == 1
    # The retired instrument starts a fresh clock, while the retained one still
    # rejects replay from before its watermark.
    assert c.observe("angel_one", tick("NIFTY", 499, 99)) is False
    assert c.observe("angel_one", tick("BANKNIFTY", 700, 201)) is True
    assert received == [("NIFTY", 500), ("BANKNIFTY", 700), ("BANKNIFTY", 700)]
    assert c.snapshot()["latest_record_count"] == 1



def test_prune_normalized_duplicate_active_keys_is_idempotent():
    c = MarketDataFailoverCoordinator(on_record=lambda _p, _r: None)
    assert c.observe("angel_one", tick("NIFTY", 600, 100)) is True
    assert c.observe("shoonya", tick("BANKNIFTY", 600, 200)) is False
    # Normalized aliases describe one retained instrument; repeated prune calls
    # do not double-count retired keys or mutate retained state.
    assert c.prune_instruments([" NIFTY ", "NIFTY", " NIFTY "]) == 1
    assert c.snapshot()["latest_record_count"] == 1
    assert c.prune_instruments(["NIFTY"]) == 0
    assert c.snapshot()["latest_record_count"] == 1
    assert c.observe("angel_one", tick("NIFTY", 600, 101)) is True



def test_prune_generator_failure_is_atomic_before_state_mutation():
    c = MarketDataFailoverCoordinator(on_record=lambda _p, _r: None)
    assert c.observe("angel_one", tick("NIFTY", 800, 100)) is True
    assert c.observe("angel_one", tick("BANKNIFTY", 900, 200)) is True

    def broken_keys():
        yield "NIFTY"
        raise RuntimeError("universe snapshot interrupted")

    try:
        c.prune_instruments(broken_keys())
    except RuntimeError as exc:
        assert "interrupted" in str(exc)
    else:
        raise AssertionError("generator failure must propagate")
    # Since the active universe was never fully validated, neither cache nor
    # timestamp watermarks should have been pruned.
    assert c.snapshot()["latest_record_count"] == 2
    assert c.observe("angel_one", tick("BANKNIFTY", 899, 199)) is False
    assert c.observe("angel_one", tick("NIFTY", 799, 99)) is False

