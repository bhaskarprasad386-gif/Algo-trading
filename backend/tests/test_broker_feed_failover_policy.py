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
    assert s.active_provider == "angel_one"
    assert s.mode == "DEGRADED_WAIT"
