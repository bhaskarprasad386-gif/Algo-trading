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


def test_negative_tick_age_is_not_accepted_as_fresh():
    p = policy()
    s = p.update(failed_angel_sockets=0, angel_tick_age_seconds=-0.1,
                 shoonya_connected=True, shoonya_tick_age_seconds=0.1, now=1)
    assert s.angel_healthy is False
    assert s.active_provider == "none"
    assert s.mode == "DEGRADED_WAIT"


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
