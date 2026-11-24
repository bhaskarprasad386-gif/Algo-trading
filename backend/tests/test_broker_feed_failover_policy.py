from app.market_data.broker_feed_failover import MarketDataFailoverPolicy


def policy():
    return MarketDataFailoverPolicy(
        angel_tick_fresh_seconds=1.0,
        shoonya_tick_fresh_seconds=1.0,
        angel_failover_stale_seconds=3.0,
        angel_failback_stability_seconds=5.0,
        minimum_shoonya_dwell_seconds=4.0,
    )


def test_single_angel_socket_failure_keeps_primary():
    p = policy()
    s = p.update(failed_angel_sockets=1, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=0)
    assert s.active_provider == "angel_one"
    assert s.mode == "PRIMARY"


def test_two_angel_sockets_failed_but_remaining_socket_is_fresh_keeps_angel():
    p = policy()
    s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=0.4,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.4, now=10)
    assert s.active_provider == "angel_one"
    assert s.mode == "PRIMARY"
    assert s.reason == "angel_healthy"


def test_two_failures_with_unready_shoonya_do_not_use_stale_feed():
    p = policy()
    s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=5.0, now=10)
    assert s.active_provider == "none"
    assert s.mode == "NO_VALID_FEED"


def test_stale_angel_feed_triggers_backup_even_if_socket_count_is_one():
    p = policy()
    s = p.update(failed_angel_sockets=1, angel_tick_age_seconds=4.5,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.3, now=20)
    assert s.active_provider == "shoonya"
    assert s.reason == "angel_feed_stale"


def test_failback_waits_for_stability_and_minimum_backup_dwell():
    p = policy()
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=12)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=15)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=17)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"


def test_stale_shoonya_during_backup_does_not_claim_live_provider():
    p = policy()
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.0,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=11)
    assert s.active_provider == "none"
    assert s.mode == "NO_VALID_FEED"


def test_negative_socket_count_rejected():
    p = policy()
    try:
        p.update(failed_angel_sockets=-1, angel_tick_age_seconds=0.1,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.1, now=1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative socket failure count must be rejected")


def test_negative_tick_age_is_rejected_without_state_mutation():
    import pytest
    p = policy()
    before = p.snapshot()
    with pytest.raises(ValueError, match="angel_tick_age_seconds"):
        p.update(failed_angel_sockets=0, angel_tick_age_seconds=-0.1,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.1, now=1)
    after = p.snapshot()
    assert after.active_provider == before.active_provider
    assert after.mode == before.mode
    assert after.reason == before.reason


def test_failback_stability_timer_resets_after_angel_feed_interrupt():
    p = policy()
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # Angel appears healthy, but the stability clock must reset when health drops.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=12)
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=14)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=16)
    assert s.active_provider == "shoonya"
    # 5s stability from 16 plus 4s minimum dwell from 10 are both required.
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=20)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=21)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"

def test_failback_does_not_trigger_before_exact_stability_and_dwell_boundary():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=101)
    # Dwell elapsed at 104, but Angel stability has only elapsed 3 seconds.
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=104)
    assert s.active_provider == "shoonya"
    # Both thresholds become satisfied exactly at 106.
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=106)
    assert s.active_provider == "angel_one"


def test_backup_switch_time_restarts_after_no_valid_feed_recovery():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=12)
    assert s.active_provider == "none"
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=30)
    assert s.active_provider == "shoonya"
    assert s.switched_at == 30


def test_failback_is_blocked_when_two_sockets_degrade_even_with_fresh_tick():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # Healthy-tick stability begins at 11, but two failed sockets reset it.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=20)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=21)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=26)
    assert s.active_provider == "angel_one"


def test_socket_failure_count_above_three_is_rejected():
    p = policy()
    try:
        p.update(failed_angel_sockets=4, angel_tick_age_seconds=0.1,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.1, now=1)
    except ValueError:
        pass
    else:
        raise AssertionError("socket failure count above the 3-socket maximum must be rejected")


def test_non_finite_failover_timing_is_rejected():
    for invalid in (float("nan"), float("inf"), float("-inf")):
        try:
            MarketDataFailoverPolicy(angel_tick_fresh_seconds=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite timing values must be rejected")


def test_backup_does_not_oscillate_on_intermittent_angel_recovery():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    # Tick freshness returns briefly, then interrupts before stability completes.
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=11)
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=14)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=15)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=19)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=20)
    assert s.active_provider == "angel_one"


def test_all_three_sockets_recover_but_failback_requires_stability_and_dwell():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=30)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=31)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=34)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=36)
    assert s.active_provider == "angel_one"


def test_out_of_order_health_event_is_rejected_without_mutating_state():
    p = policy()
    initial = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=50)
    try:
        p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=49)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-order update must be rejected")
    assert p.snapshot().active_provider == initial.active_provider
    assert p.snapshot().mode == initial.mode


def test_concurrent_same_timestamp_health_updates_preserve_consistent_state():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    p = policy()
    count = 12
    barrier = Barrier(count)

    def update_once():
        barrier.wait()
        return p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=60)

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(lambda _: update_once(), range(count)))
    assert all(result.active_provider == "angel_one" for result in results)
    snap = p.snapshot()
    assert snap.active_provider == "angel_one"
    assert snap.mode == "PRIMARY"



def test_invalid_angel_tick_age_values_are_rejected_without_state_mutation():
    import pytest

    p = policy()
    initial = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=70)
    for invalid in (-0.01, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="angel_tick_age_seconds"):
            p.update(failed_angel_sockets=3, angel_tick_age_seconds=invalid,
                    shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=71)
        snap = p.snapshot()
        assert snap.active_provider == initial.active_provider
        assert snap.mode == initial.mode



def test_invalid_shoonya_tick_age_values_are_rejected_even_when_disconnected():
    import pytest


    p = policy()
    for invalid in (-0.01, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="shoonya_tick_age_seconds"):
            p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                    shoonya_connected=False, shoonya_tick_age_seconds=invalid, now=80)



def test_missing_tick_age_is_allowed_as_unavailable_feed_evidence():
    p = policy()
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=None,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=90)
    assert s.active_provider == "shoonya"
    assert s.mode == "BACKUP"



def test_tick_freshness_and_failover_thresholds_are_inclusive_at_boundaries():
    p = policy()
    fresh = p.update(failed_angel_sockets=2, angel_tick_age_seconds=1.0,
                    shoonya_connected=True, shoonya_tick_age_seconds=1.0, now=100)
    assert fresh.active_provider == "angel_one"
    assert fresh.angel_healthy is True


    stale = p.update(failed_angel_sockets=1, angel_tick_age_seconds=3.0,
                    shoonya_connected=True, shoonya_tick_age_seconds=0.5, now=101)
    assert stale.active_provider == "shoonya"
    assert stale.reason == "angel_feed_stale"



def test_backup_socket_degradation_with_fresh_angel_tick_does_not_bypass_failback_stability():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=200)
    # Angel is delivering a fresh tick, but two failed sockets make it unstable.
    s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=201)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=202)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=207)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"



def test_simultaneous_provider_recovery_from_no_feed_is_deterministic_and_primary_wins():
    p = policy()
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=300)
    assert s.active_provider == "none"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=301)
    assert s.active_provider == "angel_one"
    assert s.mode == "PRIMARY"
    assert s.reason == "angel_recovered"



def test_backup_degrades_to_none_then_fresh_shoonya_recovery_resets_dwell_clock():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=400)
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=401)
    assert s.active_provider == "none"
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=402)
    assert s.active_provider == "shoonya"
    assert s.switched_at == 402



def test_recovery_events_with_identical_timestamp_are_serialized_consistently():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier


    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=500)
    barrier = Barrier(2)
    def healthy():
        barrier.wait()
        return p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=501)
    def degraded():
        barrier.wait()
        return p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.5,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=501)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda fn: fn(), (healthy,degraded)))
    snap=p.snapshot()
    assert snap.active_provider in ("shoonya","none")
    assert snap.mode in ("BACKUP","NO_VALID_FEED")
    assert all(r.active_provider in ("shoonya","none") for r in results)



def test_latest_timestamp_wins_concurrent_health_updates_or_older_is_rejected():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import pytest


    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=600)
    barrier = Barrier(2)
    def newer():
        barrier.wait()
        return p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=602)
    def older():
        barrier.wait()
        try:
            return p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                           shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=601)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        newer_result, older_result = list(pool.map(lambda fn: fn(), (newer, older)))
    assert newer_result.active_provider in ("angel_one", "shoonya")
    assert older_result is None or older_result.active_provider in ("shoonya", "none")
    assert p.snapshot().active_provider in ("angel_one", "shoonya")



def test_none_provider_keeps_mode_no_valid_feed_when_no_feed_recovers_within_grace():
    p = policy()
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=700)
    assert s.active_provider == "none"
    assert s.mode == "NO_VALID_FEED"
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=3.0,
                 shoonya_connected=False, shoonya_tick_age_seconds=None, now=701)
    assert s.active_provider == "none"
    assert s.mode == "NO_VALID_FEED"
    assert s.reason == "no_provider_feed_is_fresh"


def test_none_provider_validates_shoonya_recovery_timestamp_and_mode():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
             shoonya_connected=False, shoonya_tick_age_seconds=None, now=800)
    s = p.update(failed_angel_sockets=3, angel_tick_age_seconds=5.0,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=801)
    assert s.active_provider == "shoonya"
    assert s.mode == "BACKUP"
    assert s.reason == "shoonya_validated_recovery"
    assert s.switched_at == 801



def test_angel_stability_clock_resets_after_intermittent_health_during_backup():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=900)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=901)
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=904)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=905)
    assert s.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=910)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=914)
    assert s.active_provider == "angel_one"


def test_invalid_health_event_does_not_mutate_policy_snapshot():
    import pytest
    p = policy()
    before = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1000)
    with pytest.raises(ValueError):
        p.update(failed_angel_sockets=4, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1001)
    after = p.snapshot()
    assert after.active_provider == before.active_provider
    assert after.mode == before.mode
    assert after.reason == before.reason
    assert after.switched_at == before.switched_at


def test_snapshot_reads_are_repeatable_and_do_not_change_failover_state():
    p = policy()
    expected = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1100)
    reads = [p.snapshot() for _ in range(5)]
    assert all(s.active_provider == expected.active_provider for s in reads)
    assert all(s.mode == expected.mode and s.reason == expected.reason for s in reads)
    assert all(s.switched_at == expected.switched_at for s in reads)

def test_snapshot_provider_mode_pairs_never_conflict_across_transitions():
    p = policy()
    events = [
        (3, 4.0, False, None, 1200),
        (3, 4.0, True, 0.2, 1201),
        (0, 0.2, True, 0.2, 1202),
        (0, 0.2, True, 0.2, 1207),
        (0, 0.2, True, 0.2, 1210),
        (3, 4.0, False, None, 1211),
    ]
    for failed, angel_age, connected, shoonya_age, ts in events:
        s = p.update(failed_angel_sockets=failed, angel_tick_age_seconds=angel_age,
                     shoonya_connected=connected, shoonya_tick_age_seconds=shoonya_age, now=ts)
        if s.active_provider == "angel_one":
            assert s.mode == "PRIMARY"
        elif s.active_provider == "shoonya":
            assert s.mode == "BACKUP"
        else:
            assert s.mode in ("NO_VALID_FEED", "DEGRADED_WAIT")



def test_socket_failure_count_rejects_fraction_and_boolean_inputs_without_mutation():
    import pytest
    p = policy()
    baseline = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1300)
    for invalid in (1.5, True, -1, 4):
        with pytest.raises(ValueError):
            p.update(failed_angel_sockets=invalid, angel_tick_age_seconds=0.2,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1301)
        after = p.snapshot()
        assert after.active_provider == baseline.active_provider
        assert after.mode == baseline.mode
        assert after.reason == baseline.reason

def test_shoonya_connected_flag_requires_bool_and_invalid_value_is_atomic():
    import pytest
    p = policy()
    baseline = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1400)
    for invalid in ("yes", 1, None):
        with pytest.raises(ValueError):
            p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                     shoonya_connected=invalid, shoonya_tick_age_seconds=0.2, now=1401)
        after = p.snapshot()
        assert after.active_provider == baseline.active_provider
        assert after.mode == baseline.mode
        assert after.reason == baseline.reason



def test_tick_age_inputs_reject_strings_bools_and_non_numeric_values_atomically():
    import pytest
    p = policy()
    baseline = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1500)
    invalids = ("0.2", True, object())
    for invalid in invalids:
        with pytest.raises(ValueError):
            p.update(failed_angel_sockets=0, angel_tick_age_seconds=invalid,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1501)
        after = p.snapshot()
        assert after.active_provider == baseline.active_provider
        assert after.mode == baseline.mode
        assert after.reason == baseline.reason
    for invalid in invalids:
        with pytest.raises(ValueError):
            p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                     shoonya_connected=True, shoonya_tick_age_seconds=invalid, now=1501)

def test_update_timestamp_rejects_bool_and_string_types_without_mutation():
    import pytest
    p = policy()
    baseline = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1600)
    for invalid in (True, "1601", object()):
        with pytest.raises(ValueError):
            p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                     shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=invalid)
        after = p.snapshot()
        assert after.active_provider == baseline.active_provider
        assert after.mode == baseline.mode
        assert after.reason == baseline.reason


def test_failback_stability_timer_resets_after_angel_feed_flaps():
    p = policy()
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=12)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=3.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=16)
    p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=17)
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=21)
    assert s.active_provider == "shoonya"
    assert s.reason == "shoonya_active_waiting_for_angel_stability"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=22)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"


def test_snapshot_preserves_latest_health_evidence_instead_of_reporting_false_values():
    import pytest
    p = policy()
    first = p.update(
        failed_angel_sockets=2,
        angel_tick_age_seconds=0.4,
        shoonya_connected=True,
        shoonya_tick_age_seconds=0.3,
        now=2000,
    )
    snap = p.snapshot()
    assert snap == first
    assert snap.angel_healthy is True
    assert snap.shoonya_healthy is True
    assert snap.failed_angel_sockets == 2

    second = p.update(
        failed_angel_sockets=3,
        angel_tick_age_seconds=4.0,
        shoonya_connected=False,
        shoonya_tick_age_seconds=None,
        now=2001,
    )
    assert p.snapshot() == second
    assert p.snapshot().angel_healthy is False
    assert p.snapshot().shoonya_healthy is False
    assert p.snapshot().failed_angel_sockets == 3

def test_angel_stale_grace_period_waits_until_failover_boundary_without_claiming_live_feed():
    p = policy()
    # Freshness expires at 1s, but failover threshold is 3s.
    for now, age in ((1, 1.01), (2, 2.99)):
        s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=age,
                    shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=now)
        assert s.active_provider == "none"
        assert s.mode == "DEGRADED_WAIT"
        assert s.reason == "angel_tick_stale_within_failover_grace"
        assert s.shoonya_healthy is True
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=3.0,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=3)
    assert s.active_provider == "shoonya"
    assert s.mode == "BACKUP"
    assert s.reason == "angel_feed_stale"


def test_two_socket_failures_trigger_failover_at_tick_freshness_boundary():
    p = policy()
    # Two failed sockets make a stale remaining feed failover-eligible as soon
    # as freshness expires (even before the broader stale-grace threshold).
    s = p.update(
        failed_angel_sockets=2,
        angel_tick_age_seconds=1.01,
        shoonya_connected=True,
        shoonya_tick_age_seconds=0.2,
        now=50,
    )
    assert s.active_provider == "shoonya"
    assert s.mode == "BACKUP"
    assert s.reason == "two_or_more_angel_sockets_failed_and_feed_unhealthy"
    assert s.switched_at == 50


def test_no_feed_state_recovers_to_fresh_angel_even_with_all_sockets_marked_failed():
    p = policy()
    outage = p.update(
        failed_angel_sockets=3, angel_tick_age_seconds=4.2,
        shoonya_connected=False, shoonya_tick_age_seconds=None, now=10,
    )
    assert outage.active_provider == "none"
    assert outage.mode == "NO_VALID_FEED"
    # Tick freshness is the authority: socket-count degradation alone cannot
    # reject a provider that has resumed delivering fresh market data.
    recovered = p.update(
        failed_angel_sockets=3, angel_tick_age_seconds=0.2,
        shoonya_connected=False, shoonya_tick_age_seconds=None, now=11,
    )
    assert recovered.active_provider == "angel_one"
    assert recovered.mode == "PRIMARY"
    assert recovered.reason == "angel_recovered"


def test_out_of_order_health_timestamp_is_atomic_and_next_valid_update_recovers():
    import pytest
    p = policy()
    baseline = p.update(
        failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1700,
    )
    with pytest.raises(ValueError, match="out-of-order health update timestamp"):
        p.update(
            failed_angel_sockets=3, angel_tick_age_seconds=4.0,
            shoonya_connected=False, shoonya_tick_age_seconds=None, now=1699,
        )
    assert p.snapshot() == baseline
    # A rejected stale event must not advance the watermark or poison recovery.
    recovered = p.update(
        failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=1701,
    )
    assert recovered.active_provider == "angel_one"
    assert recovered.mode == "PRIMARY"
    assert recovered.reason == "angel_healthy"


def test_failback_occurs_exactly_at_both_dwell_and_stability_boundaries():
    p = policy()
    switched = p.update(
        failed_angel_sockets=2, angel_tick_age_seconds=4.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=0,
    )
    assert switched.active_provider == "shoonya"
    # Angel stability starts on the first fresh recovery tick at t=1.
    # Dwell (4s) expires at t=4, stability (5s) at t=6.
    before_boundary = p.update(
        failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=5.999,
    )
    assert before_boundary.active_provider == "shoonya"
    at_boundary = p.update(
        failed_angel_sockets=0, angel_tick_age_seconds=0.2,
        shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=6.0,
    )
    assert at_boundary.active_provider == "angel_one"
    assert at_boundary.mode == "PRIMARY"
    assert at_boundary.reason == "angel_stable_failback"


def test_shoonya_stale_tick_drops_backup_to_no_valid_feed_and_fresh_angel_recovers():
    p = policy()
    p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=10)
    no_feed = p.update(failed_angel_sockets=3, angel_tick_age_seconds=4.0,
                      shoonya_connected=True, shoonya_tick_age_seconds=2.0, now=11)
    assert no_feed.active_provider == "none"
    assert no_feed.mode == "NO_VALID_FEED"
    assert no_feed.reason == "shoonya_feed_stale"
    recovered = p.update(failed_angel_sockets=3, angel_tick_age_seconds=0.2,
                       shoonya_connected=False, shoonya_tick_age_seconds=None, now=12)
    assert recovered.active_provider == "angel_one"
    assert recovered.mode == "PRIMARY"
    assert recovered.reason == "angel_recovered"


def test_backup_does_not_failback_before_minimum_dwell_even_when_angel_stable():
    p = policy()
    p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.0,
             shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=100)
    for now in (101, 102, 103):
        s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                    shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=now)
        assert s.active_provider == "shoonya"
    at_dwell = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=104)
    assert at_dwell.active_provider == "shoonya"
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=0.2,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=105)
    assert s.active_provider == "angel_one"
    assert s.reason == "angel_stable_failback"


def test_angel_fresh_tick_with_two_failed_sockets_starts_stability_timer():
    p = policy()
    switched = p.update(failed_angel_sockets=2, angel_tick_age_seconds=4.0,
                      shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=200)
    assert switched.active_provider == "shoonya"
    for now in (201, 202, 203, 204):
        s = p.update(failed_angel_sockets=2, angel_tick_age_seconds=0.2,
                    shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=now)
        assert s.active_provider == "shoonya"
    # Socket count prevents stability; fresh ticks alone do not failback.
    assert s.reason == "shoonya_active_waiting_for_angel_stability"
    recovered = p.update(failed_angel_sockets=1, angel_tick_age_seconds=0.2,
                       shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=205)
    assert recovered.active_provider == "shoonya"
    final = p.update(failed_angel_sockets=1, angel_tick_age_seconds=0.2,
                   shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=210)
    assert final.active_provider == "angel_one"


def test_failover_when_angel_tick_age_is_missing_and_backup_fresh():
    p = policy()
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=None,
                shoonya_connected=True, shoonya_tick_age_seconds=0.2, now=300)
    assert s.active_provider == "shoonya"
    assert s.mode == "BACKUP"
    assert s.reason == "angel_feed_stale"


def test_timing_configuration_rejects_zero_negative_bool_and_nonfinite_values():
    import pytest
    for field in ("angel_tick_fresh_seconds", "shoonya_tick_fresh_seconds",
                  "angel_failover_stale_seconds", "angel_failback_stability_seconds",
                  "minimum_shoonya_dwell_seconds"):
        for invalid in (0, -1, True, float("inf"), float("nan")):
            kwargs = {field: invalid}
            with pytest.raises(ValueError, match="finite and positive"):
                MarketDataFailoverPolicy(**kwargs)
