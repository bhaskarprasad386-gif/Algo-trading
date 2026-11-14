from types import SimpleNamespace
from time import monotonic_ns, time_ns

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.provider_readiness_bridge import ProviderReadinessBridge


def _record():
    return MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "12345"),
        symbol="NIFTY26NOVFUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=time_ns(),
        ltp=25000.0,
    )


def test_shoonya_session_ack_is_scoped_to_adapter_epoch_and_subscription_stays_unverified():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 7, True, False, monotonic_ns())
    bridge.shoonya_state("LIVE", None, 7, True, True, monotonic_ns())
    bridge.shoonya_market_event(_record(), 7, True, monotonic_ns())

    snapshot = bridge.snapshot()["shoonya"]
    assert snapshot["generation"] == 7
    assert snapshot["authentication"] == "AUTHENTICATED"
    assert snapshot["authentication_evidence"] == "shoonya_ak_ack"
    assert snapshot["subscription"] == "NOT_VERIFIABLE"
    assert snapshot["feed_ready"] is False
    assert snapshot["parity_eligible"] is False
    assert snapshot["order_eligible"] is False


def test_shoonya_reconnect_invalidates_previous_epoch_evidence():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 2, True, False, monotonic_ns())
    bridge.shoonya_state("LIVE", None, 2, True, True, monotonic_ns())
    bridge.shoonya_state("RECONNECTING", "socket closed", 2, False, False, monotonic_ns())
    snapshot = bridge.snapshot()["shoonya"]
    assert snapshot["transport"] == "CLOSED"
    assert snapshot["authenticated"] is False
    assert snapshot["feed_ready"] is False


def test_angel_observer_never_promotes_authentication_and_isolated_by_socket_group():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=1)
    bridge.angel_market_event(_record(), group, 4, True, True, monotonic_ns())
    snapshots = bridge.snapshot()["angel_one"]
    state = snapshots["3:1"]
    assert state["transport"] == "OPEN"
    assert state["authentication"] == "NOT_VERIFIABLE"
    assert state["authenticated"] is False
    assert state["subscription"] == "NOT_VERIFIABLE"
    assert state["feed_ready"] is False
    assert state["parity_eligible"] is False


def test_angel_stale_group_generation_is_ignored():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=1, shard=0)
    bridge.angel_market_event(_record(), group, 5, False, True, monotonic_ns())
    bridge.angel_market_event(_record(), group, 4, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["1:0"]
    assert state["generation"] == 5
    assert state["authenticated"] is False


def test_bridge_exposes_no_pairing_scanner_or_order_activation():
    snapshot = ProviderReadinessBridge().snapshot()
    assert snapshot["mode"] == "read_only_diagnostics"
    assert snapshot["pairing_enabled"] is False
    assert snapshot["scanner_fanout_enabled"] is False
    assert snapshot["order_eligible"] is False



def test_shoonya_out_of_order_session_callback_cannot_revoke_newer_auth_state():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 9, True, False, 100)
    bridge.shoonya_state("LIVE", None, 9, True, True, 200)
    bridge.shoonya_state("AUTH_FAILED", "late callback", 9, True, False, 150)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "AUTHENTICATED"
    assert state["transport"] == "OPEN"
    assert state["stale_observations_rejected"] == 1


def test_shoonya_delayed_live_callback_cannot_resurrect_rejected_epoch():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 31, True, False, 100)
    bridge.shoonya_state("AUTH_FAILED", "bad credentials", 31, True, False, 200)
    bridge.shoonya_state("LIVE", None, 31, True, True, 300)
    state = bridge.snapshot()["shoonya"]
    assert state["generation"] == 31
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "REJECTED"
    assert state["authenticated"] is False
    assert state["stale_observations_rejected"] == 1


def test_shoonya_delayed_live_callback_cannot_resurrect_timed_out_epoch():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 32, True, False, 100)
    bridge.shoonya_state("AUTH_TIMEOUT", "no auth ack", 32, False, False, 200)
    bridge.shoonya_state("LIVE", None, 32, True, True, 300)
    state = bridge.snapshot()["shoonya"]
    assert state["generation"] == 32
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "TIMED_OUT"
    assert state["authenticated"] is False
    assert state["stale_observations_rejected"] == 1


def test_shoonya_auth_rejection_closes_transport_but_preserves_rejection_evidence():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 3, True, False, 100)
    bridge.shoonya_state("AUTH_FAILED", "bad credentials", 3, True, False, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "REJECTED"
    assert state["authenticated"] is False


def test_shoonya_out_of_order_market_observation_is_rejected():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 6, True, False, 100)
    bridge.shoonya_state("LIVE", None, 6, True, True, 200)
    bridge.shoonya_market_event(_record(), 6, True, 300)
    bridge.shoonya_market_event(_record(), 6, True, 250)
    state = bridge.snapshot()["shoonya"]
    assert state["market_event_age_ns"] is not None
    assert state["stale_observations_rejected"] == 1


def test_angel_out_of_order_observation_is_rejected_per_socket_group():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=2)
    bridge.angel_market_event(_record(), group, 8, False, True, 300)
    bridge.angel_market_event(_record(), group, 8, False, True, 250)
    state = bridge.snapshot()["angel_one"]["3:2"]
    assert state["stale_observations_rejected"] == 1
    assert state["authentication"] == "NOT_VERIFIABLE"


def test_angel_non_current_connection_observation_is_ignored():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=0)
    bridge.angel_market_event(_record(), group, 8, False, False, monotonic_ns())
    assert bridge.snapshot()["angel_one"] == {}



def test_shoonya_live_state_without_connected_transport_cannot_authenticate():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 14, True, False, 100)
    bridge.shoonya_state("LIVE", None, 14, False, True, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "UNKNOWN"
    assert state["authenticated"] is False
    assert state["feed_ready"] is False


def test_shoonya_market_event_requires_current_verified_authenticated_session():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 15, True, False, 100)
    bridge.shoonya_market_event(_record(), 15, True, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "UNKNOWN"
    assert state["market_event_age_ns"] is None
    assert state["feed_ready"] is False


def test_shoonya_market_event_after_auth_rejection_is_ignored():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 16, True, False, 100)
    bridge.shoonya_state("AUTH_FAILED", "rejected", 16, True, False, 200)
    bridge.shoonya_market_event(_record(), 16, True, 300)
    state = bridge.snapshot()["shoonya"]
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "REJECTED"
    assert state["market_event_age_ns"] is None


def test_bridge_rejects_invalid_or_future_exchange_timestamp():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=0)
    future_record = _record().model_copy(update={"timestamp_ns": time_ns() + 60_000_000_000})
    bridge.angel_market_event(future_record, group, 21, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["2:0"]
    assert state["canonical_identity_valid"] is True
    assert state["exchange_timestamp_valid"] is True
    assert state["market_event_fresh"] is False
    assert state["feed_ready"] is False


def test_bridge_rejects_missing_canonical_identity():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=1)
    invalid_record = _record().model_copy(update={"symbol": " "})
    bridge.angel_market_event(invalid_record, group, 22, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["2:1"]
    assert state["canonical_identity_valid"] is False
    assert state["feed_ready"] is False


def test_angel_transport_open_close_events_are_generation_scoped():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=4)
    bridge.angel_transport_event("OPEN", group, 31, 100)
    opened = bridge.snapshot()["angel_one"]["3:4"]
    assert opened["transport"] == "OPEN"
    assert opened["authentication"] == "NOT_VERIFIABLE"
    bridge.angel_market_event(_record(), group, 31, False, True, 200)
    assert bridge.snapshot()["angel_one"]["3:4"]["market_event_age_ns"] is not None
    bridge.angel_transport_event("CLOSED", group, 31, 300)
    closed = bridge.snapshot()["angel_one"]["3:4"]
    assert closed["transport"] == "CLOSED"
    assert closed["market_event_age_ns"] is None
    assert closed["authenticated"] is False


def test_angel_old_generation_close_cannot_close_replacement():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=1, shard=2)
    bridge.angel_transport_event("OPEN", group, 41, 100)
    bridge.angel_transport_event("OPEN", group, 42, 200)
    bridge.angel_transport_event("CLOSED", group, 41, 300)
    state = bridge.snapshot()["angel_one"]["1:2"]
    assert state["generation"] == 42
    assert state["transport"] == "OPEN"


def test_angel_closed_generation_rejects_late_market_event():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=3)
    bridge.angel_transport_event("OPEN", group, 51, 100)
    bridge.angel_transport_event("CLOSED", group, 51, 200)
    bridge.angel_market_event(_record(), group, 51, False, True, 300)
    state = bridge.snapshot()["angel_one"]["2:3"]
    assert state["transport"] == "CLOSED"
    assert state["market_event_age_ns"] is None


def test_angel_transport_event_rejects_out_of_order_lifecycle():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=4)
    bridge.angel_transport_event("OPEN", group, 61, 200)
    bridge.angel_transport_event("CLOSED", group, 61, 150)
    state = bridge.snapshot()["angel_one"]["2:4"]
    assert state["transport"] == "OPEN"
    assert state["stale_observations_rejected"] == 1


def test_angel_error_is_terminal_until_a_new_generation_opens():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=5)
    bridge.angel_transport_event("OPEN", group, 71, 100)
    bridge.angel_transport_event("ERROR", group, 71, 200)
    bridge.angel_transport_event("OPEN", group, 71, 300)
    failed = bridge.snapshot()["angel_one"]["2:5"]
    assert failed["generation"] == 71
    assert failed["transport"] == "CLOSED"
    assert failed["stale_observations_rejected"] == 1

    bridge.angel_transport_event("OPEN", group, 72, 400)
    recovered = bridge.snapshot()["angel_one"]["2:5"]
    assert recovered["generation"] == 72
    assert recovered["transport"] == "OPEN"
    assert recovered["authentication"] == "NOT_VERIFIABLE"



def test_simultaneous_broker_failures_and_recovery_remain_isolated():
    bridge = ProviderReadinessBridge()
    angel_group = SimpleNamespace(mode=3, shard=1)

    # Both brokers establish their own current-generation transport evidence.
    bridge.shoonya_state("AUTHENTICATING", None, 101, True, False, 100)
    bridge.shoonya_state("LIVE", None, 101, True, True, 200)
    bridge.angel_transport_event("OPEN", angel_group, 201, 300)

    # Fail both providers independently. Neither failure may rewrite the other's state.
    bridge.shoonya_state("AUTH_FAILED", "Shoonya auth rejected", 101, True, False, 400)
    bridge.angel_transport_event("ERROR", angel_group, 201, 500)
    failed = bridge.snapshot()
    assert failed["shoonya"]["transport"] == "CLOSED"
    assert failed["shoonya"]["authentication"] == "REJECTED"
    assert failed["angel_one"]["3:1"]["transport"] == "CLOSED"
    assert failed["angel_one"]["3:1"]["authentication"] == "NOT_VERIFIABLE"

    # A fresh Angel socket generation must not resurrect Shoonya's rejected epoch.
    bridge.angel_transport_event("OPEN", angel_group, 202, 600)
    after_angel_recovery = bridge.snapshot()
    assert after_angel_recovery["angel_one"]["3:1"]["generation"] == 202
    assert after_angel_recovery["angel_one"]["3:1"]["transport"] == "OPEN"
    assert after_angel_recovery["shoonya"]["generation"] == 101
    assert after_angel_recovery["shoonya"]["transport"] == "CLOSED"
    assert after_angel_recovery["shoonya"]["authentication"] == "REJECTED"

    # A new Shoonya epoch can authenticate without changing Angel's new generation.
    bridge.shoonya_state("AUTHENTICATING", None, 102, True, False, 700)
    bridge.shoonya_state("LIVE", None, 102, True, True, 800)
    # Delayed failure callbacks from both retired generations are ignored.
    bridge.shoonya_state("AUTH_FAILED", "late old rejection", 101, True, False, 900)
    bridge.angel_transport_event("CLOSED", angel_group, 201, 1000)
    recovered = bridge.snapshot()
    assert recovered["shoonya"]["generation"] == 102
    assert recovered["shoonya"]["authentication"] == "AUTHENTICATED"
    assert recovered["shoonya"]["transport"] == "OPEN"
    assert recovered["angel_one"]["3:1"]["generation"] == 202
    assert recovered["angel_one"]["3:1"]["transport"] == "OPEN"


def test_angel_socket_group_failure_does_not_close_other_group():
    bridge = ProviderReadinessBridge()
    first = SimpleNamespace(mode=1, shard=0)
    second = SimpleNamespace(mode=3, shard=0)
    bridge.angel_transport_event("OPEN", first, 11, 100)
    bridge.angel_transport_event("OPEN", second, 21, 200)
    bridge.angel_transport_event("CLOSED", first, 11, 300)

    states = bridge.snapshot()["angel_one"]
    assert states["1:0"]["transport"] == "CLOSED"
    assert states["3:0"]["transport"] == "OPEN"
    assert states["3:0"]["generation"] == 21


def test_manager_transport_observer_and_shoonya_epoch_recovery_are_integrated():
    """Exercise the manager's real observer gate against the dual-provider reducer."""
    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup

    bridge = ProviderReadinessBridge()
    manager = CommonWebSocketManager()
    group = SocketGroup(3, 7)
    manager.register_transport_observer(
        "provider-readiness-integration", bridge.angel_transport_event
    )

    # The manager only publishes lifecycle events for its active socket generation.
    first_socket = object()
    with manager._lock:
        manager._sockets[group] = first_socket
        manager._socket_generation[group] = 10
        manager._socket_generation_counter[group] = 10
    manager._notify_transport_observers("OPEN", group, 10, 100)

    # Replace the socket. A late close from generation 10 must not reach the bridge.
    second_socket = object()
    with manager._lock:
        manager._sockets[group] = second_socket
        manager._socket_generation[group] = 11
        manager._socket_generation_counter[group] = 11
    manager._notify_transport_observers("OPEN", group, 11, 200)
    manager._notify_transport_observers("CLOSED", group, 10, 300)

    # Shoonya can independently fail and recover without changing Angel's shard.
    bridge.shoonya_state("AUTHENTICATING", None, 20, True, False, 400)
    bridge.shoonya_state("AUTH_FAILED", "rejected", 20, True, False, 500)
    bridge.shoonya_state("AUTHENTICATING", None, 21, True, False, 600)
    bridge.shoonya_state("LIVE", None, 21, True, True, 700)

    # The active Angel generation closes; a later stale OPEN is not accepted.
    manager._notify_transport_observers("CLOSED", group, 11, 800)
    manager._notify_transport_observers("OPEN", group, 11, 900)
    state = bridge.snapshot()
    assert state["angel_one"]["3:7"]["generation"] == 11
    assert state["angel_one"]["3:7"]["transport"] == "CLOSED"
    assert state["angel_one"]["3:7"]["authentication"] == "NOT_VERIFIABLE"
    assert state["shoonya"]["generation"] == 21
    assert state["shoonya"]["transport"] == "OPEN"
    assert state["shoonya"]["authentication"] == "AUTHENTICATED"
    assert state["pairing_enabled"] is False
    assert state["scanner_fanout_enabled"] is False
    assert state["order_eligible"] is False
    # These are sentinel socket objects, not SDK sockets; detach them before
    # manager shutdown so the test exercises no fake close implementation.
    with manager._lock:
        manager._sockets.clear()
        manager._socket_generation.clear()
    manager.close()


def test_dual_broker_reconnect_storm_keeps_only_latest_generations_authoritative():
    """Replay many reconnect/failure cycles and late callbacks without broker I/O."""
    bridge = ProviderReadinessBridge()
    groups = [
        SimpleNamespace(mode=1, shard=0),
        SimpleNamespace(mode=3, shard=1),
        SimpleNamespace(mode=5, shard=2),
    ]

    # Angel: each shard repeatedly opens, errors, rejects same-generation
    # resurrection, then advances. Delayed close from the retired generation
    # must never close the replacement.
    for cycle in range(1, 65):
        for offset, group in enumerate(groups):
            generation = cycle * 100 + offset + 1
            base = cycle * 10_000 + offset * 100
            bridge.angel_transport_event("OPEN", group, generation, base + 1)
            bridge.angel_transport_event("ERROR", group, generation, base + 2)
            bridge.angel_transport_event("OPEN", group, generation, base + 3)
            bridge.angel_transport_event("OPEN", group, generation + 1, base + 4)
            bridge.angel_transport_event("CLOSED", group, generation, base + 5)

    # Shoonya: authenticate each epoch, terminally reject it, then start a
    # higher epoch. A late LIVE from the retired epoch must not authenticate it.
    for epoch in range(1, 65):
        received = epoch * 1_000
        bridge.shoonya_state("AUTHENTICATING", None, epoch, True, False, received + 1)
        bridge.shoonya_state("LIVE", None, epoch, True, True, received + 2)
        bridge.shoonya_state("AUTH_TIMEOUT", "simulated timeout", epoch, False, False, received + 3)
        bridge.shoonya_state("LIVE", None, epoch, True, True, received + 4)

    # Final epoch recovers; late callbacks from the 64th epoch cannot regress it.
    bridge.shoonya_state("AUTHENTICATING", None, 65, True, False, 65_001)
    bridge.shoonya_state("LIVE", None, 65, True, True, 65_002)
    bridge.shoonya_state("AUTH_FAILED", "late retired failure", 64, True, False, 65_003)

    snapshot = bridge.snapshot()
    for offset, group in enumerate(groups):
        key = f"{group.mode}:{group.shard}"
        state = snapshot["angel_one"][key]
        assert state["generation"] == 64 * 100 + offset + 2
        assert state["transport"] == "OPEN"
        assert state["authentication"] == "NOT_VERIFIABLE"
        assert state["feed_ready"] is False
        assert state["order_eligible"] is False

    shoonya = snapshot["shoonya"]
    assert shoonya["generation"] == 65
    assert shoonya["transport"] == "OPEN"
    assert shoonya["authentication"] == "AUTHENTICATED"
    assert shoonya["authenticated"] is True
    assert shoonya["feed_ready"] is False
    assert shoonya["parity_eligible"] is False
    assert snapshot["pairing_enabled"] is False
    assert snapshot["scanner_fanout_enabled"] is False
    assert snapshot["order_eligible"] is False


def test_threaded_dual_broker_lifecycle_updates_are_isolated_and_generation_fenced():
    """Run broker lifecycle callbacks concurrently across real Python threads."""
    from threading import Barrier, Thread

    bridge = ProviderReadinessBridge()
    groups = [
        SimpleNamespace(mode=1, shard=0),
        SimpleNamespace(mode=3, shard=1),
        SimpleNamespace(mode=5, shard=2),
    ]
    start = Barrier(4)
    failures = []

    def angel_worker(group, seed):
        try:
            start.wait()
            received = monotonic_ns()
            for cycle in range(1, 41):
                generation = seed + cycle * 10
                received += 1
                bridge.angel_transport_event("OPEN", group, generation, received)
                received += 1
                bridge.angel_transport_event("ERROR", group, generation, received)
                received += 1
                bridge.angel_transport_event("OPEN", group, generation + 1, received)
                received += 1
                # A delayed close from the retired generation must not close
                # the replacement even while other providers update concurrently.
                bridge.angel_transport_event("CLOSED", group, generation, received)
        except BaseException as exc:
            failures.append(exc)

    def shoonya_worker():
        try:
            start.wait()
            received = monotonic_ns()
            for epoch in range(1, 41):
                received += 1
                bridge.shoonya_state("AUTHENTICATING", None, epoch, True, False, received)
                received += 1
                bridge.shoonya_state("LIVE", None, epoch, True, True, received)
                received += 1
                bridge.shoonya_state("AUTH_TIMEOUT", "threaded test timeout", epoch, False, False, received)
                received += 1
                bridge.shoonya_state("LIVE", None, epoch, True, True, received)
        except BaseException as exc:
            failures.append(exc)

    workers = [
        Thread(target=angel_worker, args=(groups[0], 1000), name="angel-mode1-race"),
        Thread(target=angel_worker, args=(groups[1], 2000), name="angel-mode3-race"),
        Thread(target=angel_worker, args=(groups[2], 3000), name="angel-mode5-race"),
        Thread(target=shoonya_worker, name="shoonya-epoch-race"),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers), "lifecycle worker did not terminate"
    assert failures == []

    snapshot = bridge.snapshot()
    for group, seed in zip(groups, (1000, 2000, 3000)):
        state = snapshot["angel_one"][f"{group.mode}:{group.shard}"]
        assert state["generation"] == seed + 401
        assert state["transport"] == "OPEN"
        assert state["authentication"] == "NOT_VERIFIABLE"
        assert state["feed_ready"] is False
        assert state["order_eligible"] is False

    shoonya = snapshot["shoonya"]
    assert shoonya["generation"] == 40
    assert shoonya["transport"] == "CLOSED"
    assert shoonya["authentication"] == "TIMED_OUT"
    assert shoonya["authenticated"] is False
    assert shoonya["feed_ready"] is False
    assert snapshot["pairing_enabled"] is False
    assert snapshot["scanner_fanout_enabled"] is False
    assert snapshot["order_eligible"] is False


def test_manager_shutdown_fences_concurrent_late_transport_open_callback():
    """A callback racing manager.close cannot resurrect readiness after shutdown."""
    from threading import Event, Thread

    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup

    bridge = ProviderReadinessBridge()
    close_entered = Event()
    release_close = Event()
    observed = []
    group = SocketGroup(3, 9)

    class BlockingCloseSocket:
        def close(self):
            close_entered.set()
            assert release_close.wait(timeout=3)

    manager = CommonWebSocketManager()
    manager.register_transport_observer(
        "shutdown-race-readiness", bridge.angel_transport_event
    )
    manager._transport_observers["shutdown-race-observed"] = (
        lambda *args: observed.append(args)
    )
    socket = BlockingCloseSocket()
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_generation[group] = 77
        manager._socket_generation_counter[group] = 77

    manager._notify_transport_observers("OPEN", group, 77, 100)
    assert bridge.snapshot()["angel_one"]["3:9"]["transport"] == "OPEN"

    shutdown = Thread(target=manager.close, name="manager-shutdown-race")
    shutdown.start()
    try:
        assert close_entered.wait(timeout=1), "manager did not begin socket retirement"
        # close() has atomically marked the manager closed and removed the
        # active generation before waiting for the SDK close worker.
        manager._notify_transport_observers("OPEN", group, 77, 200)
        manager._notify_transport_observers("CLOSED", group, 77, 300)
        during_shutdown = bridge.snapshot()
        assert during_shutdown["angel_one"]["3:9"]["transport"] == "CLOSED"
        assert during_shutdown["angel_one"]["3:9"]["generation"] == 77
        assert observed == [("OPEN", group, 77, 100), ("CLOSED", group, 77, 300)]
    finally:
        release_close.set()
        shutdown.join(timeout=3)

    assert not shutdown.is_alive(), "manager shutdown worker did not terminate"
    final = bridge.snapshot()
    assert final["angel_one"]["3:9"]["transport"] == "CLOSED"
    assert final["angel_one"]["3:9"]["feed_ready"] is False
    assert final["pairing_enabled"] is False
    assert final["scanner_fanout_enabled"] is False
    assert final["order_eligible"] is False

def test_manager_shutdown_serializes_with_inflight_recovery_and_closes_replacement():
    """A recovery pass cannot leave a replacement socket alive after close."""
    from threading import Event, Thread

    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
    from app.market_data.contracts import InstrumentKey
    from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry

    class LifecycleSocket:
        instances = []

        def __init__(self):
            self.connected = False
            self.connecting = False
            self.closed = False
            type(self).instances.append(self)

        def connect(self, **kwargs):
            self.connected = True

        def subscribe_groups(self, groups, mode=None):
            pass

        def unsubscribe_groups(self, groups):
            pass

        def close(self):
            self.closed = True
            self.connected = False

    LifecycleSocket.instances.clear()
    registry = InstrumentRegistry()
    key = InstrumentKey("NSE", "EQ", "123456")
    registry.register(InstrumentDescriptor(
        key=key, symbol="TEST", instrument_type="equity",
        exchange="NSE", segment="EQ",
    ))
    manager = CommonWebSocketManager(registry, socket_factory=LifecycleSocket)
    manager._recovery_interval_seconds = 60.0
    recovery_close_entered = Event()
    release_recovery_close = Event()
    shutdown_started = Event()
    recovery_results = []
    failures = []
    original_close_tracked = manager._close_socket_tracked

    def gated_close_tracked(*args, **kwargs):
        if kwargs.get("reason") == "stale_recovery":
            recovery_close_entered.set()
            if not release_recovery_close.wait(timeout=3):
                raise AssertionError("recovery close gate was not released")
        return original_close_tracked(*args, **kwargs)

    manager._close_socket_tracked = gated_close_tracked
    try:
        manager.subscribe("concurrency-regression", [key])
        group = SocketGroup(1, 0)
        original_socket = manager._sockets[group]
        # Force the existing generation into the disconnected recovery path.
        original_socket.connected = False
        manager._socket_created_at[group] = monotonic_ns() / 1_000_000_000 - 2.0

        def recover():
            try:
                recovery_results.append(manager.recover_disconnected(
                    min_age_seconds=0.0, silent_age_seconds=-1.0
                ))
            except BaseException as exc:
                failures.append(exc)

        def shutdown():
            shutdown_started.set()
            try:
                manager.close()
            except BaseException as exc:
                failures.append(exc)

        recovery_thread = Thread(target=recover, name="recovery-close-race")
        recovery_thread.start()
        assert recovery_close_entered.wait(timeout=2), "recovery did not reach retirement"
        shutdown_thread = Thread(target=shutdown, name="manager-close-race")
        shutdown_thread.start()
        assert shutdown_started.wait(timeout=1), "shutdown thread did not start"
        release_recovery_close.set()
        recovery_thread.join(timeout=4)
        shutdown_thread.join(timeout=4)

        assert not recovery_thread.is_alive(), "recovery deadlocked with shutdown"
        assert not shutdown_thread.is_alive(), "shutdown deadlocked with recovery"
        assert failures == []
        assert recovery_results == [1]
        assert len(LifecycleSocket.instances) == 2
        assert all(item.closed and not item.connected for item in LifecycleSocket.instances)
        final = manager.snapshot()
        assert final["socket_groups"] == 0
        assert final["connected_groups"] == []
        assert manager._closed is True
    finally:
        release_recovery_close.set()
        manager.close()

def test_manager_observer_concurrent_three_shard_recovery_storm_isolated():
    """Drive three manager observer streams concurrently through 40 generations."""
    from threading import Barrier, Thread

    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup

    bridge = ProviderReadinessBridge()
    manager = CommonWebSocketManager()
    manager.register_transport_observer(
        "three-shard-concurrency-regression", bridge.angel_transport_event
    )
    groups = [SocketGroup(1, shard) for shard in range(3)]
    failures = []
    barrier = Barrier(len(groups))
    generations = 40

    class NoopSocket:
        def close(self):
            pass

    def drive_group(group):
        try:
            barrier.wait(timeout=2)
            for cycle in range(1, generations + 1):
                generation = cycle * 2 - 1
                replacement = generation + 1
                # Publish a new active manager generation before its OPEN event.
                with manager._lock:
                    manager._sockets[group] = (generation, NoopSocket())
                    manager._socket_generation[group] = generation
                    manager._socket_generation_counter[group] = generation
                manager._notify_transport_observers(
                    "OPEN", group, generation, cycle * 10 + group.shard
                )
                manager._notify_transport_observers(
                    "ERROR", group, generation, cycle * 10 + 1 + group.shard
                )
                # ERROR is terminal for this generation; same-generation OPEN
                # must not resurrect it.
                manager._notify_transport_observers(
                    "OPEN", group, generation, cycle * 10 + 2 + group.shard
                )
                # Retire the generation in the manager and publish its successor.
                with manager._lock:
                    manager._sockets[group] = (replacement, NoopSocket())
                    manager._socket_generation[group] = replacement
                    manager._socket_generation_counter[group] = replacement
                manager._notify_transport_observers(
                    "OPEN", group, replacement, cycle * 10 + 3 + group.shard
                )
                # The manager's observer gate must suppress this retired close.
                manager._notify_transport_observers(
                    "CLOSED", group, generation, cycle * 10 + 4 + group.shard
                )
        except BaseException as exc:
            failures.append(exc)

    workers = [
        Thread(target=drive_group, args=(group,), name=f"angel-shard-{group.shard}")
        for group in groups
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        assert all(not worker.is_alive() for worker in workers), "shard lifecycle worker deadlocked"
        assert failures == []
        state = bridge.snapshot()
        for group in groups:
            key = f"{group.mode}:{group.shard}"
            shard = state["angel_one"][key]
            assert shard["generation"] == generations * 2
            assert shard["transport"] == "OPEN"
            assert shard["authentication"] == "NOT_VERIFIABLE"
            assert shard["feed_ready"] is False
        assert state["pairing_enabled"] is False
        assert state["scanner_fanout_enabled"] is False
        assert state["order_eligible"] is False
    finally:
        manager.close()


def test_manager_close_waits_for_pre_admitted_transport_observer_callback():
    """Shutdown cannot return while a pre-admitted OPEN observer is still executing."""
    from threading import Event, Thread

    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup

    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    callback_entered = Event()
    release_callback = Event()
    close_finished = Event()
    calls = []

    def observer(event, observed_group, generation, received_ns):
        calls.append((event, observed_group, generation))
        callback_entered.set()
        assert release_callback.wait(timeout=3), "observer release gate timed out"

    manager.register_transport_observer("blocking-regression", observer)
    with manager._lock:
        manager._sockets[group] = object()
        manager._socket_generation[group] = 7
        manager._socket_generation_counter[group] = 7

    notify_thread = Thread(
        target=lambda: manager._notify_transport_observers("OPEN", group, 7, 100),
        name="pre-admitted-transport-open",
    )
    close_errors = []

    def close_manager():
        try:
            manager.close()
        except BaseException as exc:
            close_errors.append(exc)
        finally:
            close_finished.set()

    try:
        notify_thread.start()
        assert callback_entered.wait(timeout=2), "OPEN observer was not invoked"
        close_thread = Thread(target=close_manager, name="observer-dispatch-shutdown")
        close_thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with manager._lock:
                if manager._closed:
                    break
            time.sleep(0.005)
        with manager._lock:
            assert manager._closed is True
        assert not close_finished.wait(timeout=0.05), "close returned before observer callback completed"
        release_callback.set()
        notify_thread.join(timeout=2)
        close_thread.join(timeout=2)
        assert not notify_thread.is_alive()
        assert not close_thread.is_alive()
        assert close_errors == []
        assert close_finished.is_set()
        manager._notify_transport_observers("OPEN", group, 7, 200)
        assert calls == [("OPEN", group, 7)]
    finally:
        release_callback.set()
        manager.close()
