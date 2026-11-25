from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


class RouteSocket:
    instances = []

    def __init__(self):
        self.connect_calls = []
        self.closed = False
        self.connected = False
        self.connecting = False
        type(self).instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connected = True
        self.connecting = False

    def subscribe_groups(self, groups, mode=None):
        pass

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True
        self.connected = False


def _descriptor(token: str, *, exchange: str = "NFO") -> InstrumentDescriptor:
    # Only the explicit locked contract names receive a preferred lane. Generic
    # fixtures remain adaptive so these tests continue to exercise legacy feeds.
    if token.startswith("NIFTY"):
        underlying, expiry_type = "NIFTY", "WEEKLY"
    elif token.startswith("BANK"):
        underlying, expiry_type = "BANKNIFTY", "MONTHLY"
    else:
        underlying, expiry_type = "OTHER", None
    return InstrumentDescriptor(
        key=InstrumentKey(exchange, exchange, token),
        symbol=f"OPT-{token}",
        instrument_type="option",
        exchange=exchange,
        segment=exchange,
        underlying=underlying,
        expiry_type=expiry_type,
        expiry="2026-12-31",
        strike=25000.0,
        option_type="CE",
    )


def setup_function():
    RouteSocket.instances.clear()


def test_one_options_consumer_routes_instruments_from_multiple_socket_shards():
    registry = InstrumentRegistry()
    nifty_weekly = _descriptor("NIFTY-W")
    banknifty_monthly = _descriptor("BANK-M")
    registry.register_many((nifty_weekly, banknifty_monthly))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0
    delivered = []
    manager.register_callback("shared-index-market-data:options", lambda message: delivered.append(message["token"]))

    try:
        manager.subscribe(
            "shared-index-market-data:options",
            (nifty_weekly.key, banknifty_monthly.key),
            mode=3,
        )

        assert set(manager._route_index) == {SocketGroup(3, 0), SocketGroup(3, 1)}
        group_by_token = {
            token: group
            for group, routes in manager._route_index.items()
            for (_exchange_type, token) in routes
        }
        assert set(group_by_token) == {"BANK-M", "NIFTY-W"}
        assert group_by_token["BANK-M"] != group_by_token["NIFTY-W"]
        assert sum(len(routes) for routes in manager._route_index.values()) == 2

        for token, group in group_by_token.items():
            pair = next(pair for pair in manager._route_index[group] if pair[1] == token)
            manager._on_data(group, {"token": token, "exchange_type": pair[0], "ltp": 10000})

        assert sorted(delivered) == ["BANK-M", "NIFTY-W"]
    finally:
        manager.close()


def test_fourth_mode_rejection_preserves_live_assignments_routes_and_sockets():
    registry = InstrumentRegistry()
    descriptors = [_descriptor(f"TOKEN-{mode}") for mode in (1, 2, 3, 4)]
    registry.register_many(descriptors)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    delivered = []
    for mode in (1, 2, 3, 4):
        manager.register_callback(f"consumer-{mode}", lambda message, out=delivered: out.append(message["token"]))

    try:
        for mode, item in zip((1, 2, 3), descriptors[:3]):
            manager.subscribe(f"consumer-{mode}", (item.key,), mode=mode)

        old_assignments = {mode: dict(values) for mode, values in manager._shard_assignments.items()}
        old_routes = {group: dict(routes) for group, routes in manager._route_index.items()}
        old_sockets = set(manager._sockets)

        try:
            manager.subscribe("consumer-4", (descriptors[3].key,), mode=4)
        except ValueError as exc:
            assert "global WebSocket limit exceeded" in str(exc)
        else:
            raise AssertionError("a fourth physical mode/shard group must be rejected")

        assert manager._shard_assignments == old_assignments
        assert manager._route_index == old_routes
        assert set(manager._sockets) == old_sockets
        assert manager.snapshot()["socket_groups"] == 3

        first = descriptors[0]
        manager._on_data(SocketGroup(1, 0), {"token": first.key.token, "exchange_type": 2, "ltp": 10000})
        assert delivered == [first.key.token]
    finally:
        manager.close()


def test_active_pair_assignments_remain_stable_when_consumer_adds_an_instrument():
    registry = InstrumentRegistry()
    items = [_descriptor(token) for token in ("A", "B", "C")]
    registry.register_many(items)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("options", (items[0].key, items[1].key), mode=3)
        before = dict(manager._shard_assignments[3])
        assert set(before) == {(2, "A"), (2, "B")}

        manager.subscribe("options", (items[2].key,), mode=3)
        after = manager._shard_assignments[3]

        assert after[(2, "A")] == before[(2, "A")]
        assert after[(2, "B")] == before[(2, "B")]
        assert after[(2, "C")] == 2
        assert set(manager._route_index) == {
            SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
        }
    finally:
        manager.close()


def test_route_projection_keeps_multiple_consumers_for_shared_instrument_across_shard():
    registry = InstrumentRegistry()
    first = _descriptor("SHARED-W")
    second = _descriptor("OTHER-M")
    registry.register_many((first, second))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0
    delivered = []
    manager.register_callback("options-owner", lambda message: delivered.append(("options", message["token"])))
    manager.register_callback("observer-owner", lambda message: delivered.append(("observer", message["token"])))

    try:
        manager.subscribe("options-owner", (first.key, second.key), mode=3)
        manager.subscribe("observer-owner", (first.key,), mode=3)

        first_pair = (2, first.key.token)
        first_group = next(
            group for group, routes in manager._route_index.items()
            if first_pair in routes
        )
        assert set(manager._route_index[first_group][first_pair]) == {
            ("options-owner", first.key),
            ("observer-owner", first.key),
        }

        manager._on_data(first_group, {"token": first.key.token, "exchange_type": 2, "ltp": 10000})
        assert sorted(delivered) == [
            ("observer", first.key.token),
            ("options", first.key.token),
        ]
    finally:
        manager.close()


def test_consumer_route_survives_missing_socket_recovery_and_replacement():
    registry = InstrumentRegistry()
    items = [_descriptor("RECOVER-A"), _descriptor("RECOVER-B")]
    registry.register_many(items)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0
    delivered = []
    manager.register_callback("options-owner", lambda message: delivered.append(message["token"]))

    try:
        manager.subscribe("options-owner", (items[0].key, items[1].key), mode=3)
        target_group = next(
            group for group, routes in manager._route_index.items()
            if (2, items[1].key.token) in routes
        )
        with manager._lock:
            manager._sockets.pop(target_group)
            manager._socket_tokens.pop(target_group, None)
            manager._socket_created_at.pop(target_group, None)

        assert target_group in manager._route_index
        assert manager.snapshot()["missing_socket_groups"] == [f"{target_group.mode}:{target_group.shard}"]

        assert manager.recover_disconnected(min_age_seconds=10.0) == 0
        assert target_group in manager._sockets
        assert manager.snapshot()["missing_socket_groups"] == []

        manager._on_data(
            target_group,
            {"token": items[1].key.token, "exchange_type": 2, "ltp": 10000},
        )
        assert delivered == [items[1].key.token]
    finally:
        manager.close()



def test_subscribe_and_unsubscribe_are_serialized_as_one_registry_route_transition():
    entered_registry_subscribe = __import__("threading").Event()
    release_registry_subscribe = __import__("threading").Event()
    unsubscribe_started = __import__("threading").Event()

    class BlockingRegistry(InstrumentRegistry):
        def subscribe(self, consumer, key, mode=1):
            if consumer == "racing-owner":
                entered_registry_subscribe.set()
                assert release_registry_subscribe.wait(timeout=2.0)
            return super().subscribe(consumer, key, mode)

    registry = BlockingRegistry()
    item = _descriptor("SERIALIZED-MUTATION")
    registry.register(item)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    errors = []

    def subscribe_owner():
        try:
            manager.subscribe("racing-owner", (item.key,), mode=3)
        except Exception as exc:
            errors.append(exc)

    def unsubscribe_owner():
        unsubscribe_started.set()
        try:
            manager.unsubscribe("racing-owner", (item.key,))
        except Exception as exc:
            errors.append(exc)

    first = __import__("threading").Thread(target=subscribe_owner, daemon=True)
    second = __import__("threading").Thread(target=unsubscribe_owner, daemon=True)
    try:
        first.start()
        assert entered_registry_subscribe.wait(timeout=1.0)
        second.start()
        assert unsubscribe_started.wait(timeout=1.0)

        # The first mutation owns the reconciliation transaction while paused
        # inside registry.subscribe; the second cannot partially mutate routes.
        assert registry.subscriptions() == ()

        release_registry_subscribe.set()
        first.join(timeout=2.0)
        second.join(timeout=2.0)
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert registry.subscriptions() == ()
        assert manager.snapshot()["active_instruments"] == 0
        assert manager.snapshot()["required_socket_groups"] == []
        assert manager.snapshot()["missing_socket_groups"] == []
    finally:
        release_registry_subscribe.set()
        manager.close()


def test_live_allocator_pins_locked_option_contracts_by_instrument_not_consumer():
    registry = InstrumentRegistry()
    nifty = _descriptor("NIFTY-WEEKLY-1")
    bank = _descriptor("BANK-MONTHLY-1")
    registry.register_many((nifty, bank))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("shared-index-market-data:options", (nifty.key, bank.key), mode=3)
        assert manager._shard_assignments[3] == {
            (2, nifty.key.token): 0,
            (2, bank.key.token): 1,
        }
        snapshot = manager.snapshot()
        roster = {item["token"]: item["socket_group"] for item in snapshot["subscription_roster"]}
        assert roster[nifty.key.token] == "3:0"
        assert roster[bank.key.token] == "3:1"
        assert set(manager._route_index) == {SocketGroup(3, 0), SocketGroup(3, 1)}
    finally:
        manager.close()


def test_adaptive_instruments_never_share_a_reserved_locked_lane():
    registry = InstrumentRegistry()
    nifty = _descriptor("NIFTY-WEEKLY-2")
    adaptive = _descriptor("FINNIFTY-OPTION")
    registry.register_many((nifty, adaptive))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("locked-options", (nifty.key,), mode=3)
        manager.subscribe("other-index", (adaptive.key,), mode=3)
        assert manager._shard_assignments[3] == {
            (2, nifty.key.token): 0,
            (2, adaptive.key.token): 1,
        }
        assert manager.snapshot()["subscription_roster"][0]["socket_group"] in {"3:0", "3:1"}
        assert manager._shard_assignments[3][(2, adaptive.key.token)] != 0
    finally:
        manager.close()


def test_same_broker_pair_with_locked_and_unclassified_descriptors_fails_closed():
    registry = InstrumentRegistry()
    locked = _descriptor("NIFTY-COLLISION")
    # Same exchange/token pair but a different instrument key/metadata must not
    # inherit the locked route accidentally.
    ambiguous = InstrumentDescriptor(
        key=InstrumentKey("NFO", "DERIV", locked.key.token),
        symbol="UNCLASSIFIED-COLLISION",
        instrument_type="option",
        exchange="NFO",
        segment="DERIV",
        underlying="FINNIFTY",
        expiry="2026-12-31",
    )
    registry.register_many((locked, ambiguous))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        try:
            manager.subscribe("locked-owner", (locked.key,), mode=3)
            manager.subscribe("other-owner", (ambiguous.key,), mode=3)
        except ValueError as exc:
            assert "ambiguous broker token affinity" in str(exc)
        else:
            raise AssertionError("ambiguous broker token identity must fail closed")
        assert len(manager._sockets) == 1
        assert manager._route_index == {SocketGroup(3, 0): {(2, locked.key.token): [("locked-owner", locked.key)]}}
    finally:
        manager.close()


def test_locked_three_lane_budget_rejects_unclassified_instruments_without_lane_mixing():
    registry = InstrumentRegistry()

    def index(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NSE", "NSE", token),
            symbol=underlying,
            instrument_type="index",
            exchange="NSE",
            segment="NSE",
            underlying=underlying,
        )

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=token,
            instrument_type="future",
            exchange="NFO",
            segment="NFO",
            underlying=underlying,
            expiry="2026-12-31",
        )

    nifty_spot = index("NIFTY-SPOT", "NIFTY")
    bank_spot = index("BANK-SPOT", "BANKNIFTY")
    nifty_future = future("NIFTY-FUT", "NIFTY")
    bank_future = future("BANK-FUT", "BANKNIFTY")
    other_index = index("FINNIFTY-SPOT", "FINNIFTY")
    registry.register_many((nifty_spot, bank_spot, nifty_future, bank_future, other_index))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("spot-owner", (nifty_spot.key, bank_spot.key), mode=3)
        manager.subscribe("future-owner", (nifty_future.key, bank_future.key), mode=3)
        expected = {
            (1, nifty_spot.key.token): 0,
            (1, bank_spot.key.token): 1,
            (2, nifty_future.key.token): 2,
            (2, bank_future.key.token): 2,
        }
        assert manager._shard_assignments[3] == expected
        old_routes = {group: dict(routes) for group, routes in manager._route_index.items()}
        old_sockets = set(manager._sockets)

        try:
            manager.subscribe("other-index-owner", (other_index.key,), mode=3)
        except ValueError as exc:
            assert "global WebSocket limit exceeded" in str(exc)
        else:
            raise AssertionError("unsupported instrument must not be mixed into a locked lane")

        assert manager._shard_assignments[3] == expected
        assert manager._route_index == old_routes
        assert set(manager._sockets) == old_sockets
        assert set(manager._route_index) == {
            SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
        }
    finally:
        manager.close()


def test_locked_affinity_is_idempotent_across_reconcile_and_partial_owner_release():
    registry = InstrumentRegistry()
    nifty_option = _descriptor("NIFTY-WEEKLY-IDEMPOTENT")
    bank_option = _descriptor("BANK-MONTHLY-IDEMPOTENT")

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=token,
            instrument_type="future",
            exchange="NFO",
            segment="NFO",
            underlying=underlying,
            expiry="2026-12-31",
        )

    nifty_future = future("NIFTY-FUT-IDEMPOTENT", "NIFTY")
    bank_future = future("BANK-FUT-IDEMPOTENT", "BANKNIFTY")
    registry.register_many((nifty_option, bank_option, nifty_future, bank_future))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("shared-options-owner", (nifty_option.key, bank_option.key), mode=3)
        manager.subscribe("shared-futures-owner", (nifty_future.key, bank_future.key), mode=3)
        expected = {
            (2, nifty_option.key.token): 0,
            (2, bank_option.key.token): 1,
            (2, nifty_future.key.token): 2,
            (2, bank_future.key.token): 2,
        }
        assert manager._shard_assignments[3] == expected

        first_routes = {group: dict(routes) for group, routes in manager._route_index.items()}
        manager._reconcile()
        manager._reconcile()
        assert manager._shard_assignments[3] == expected
        assert manager._route_index == first_routes
        assert {
            row["token"]: row["socket_group"]
            for row in manager.snapshot()["subscription_roster"]
        } == {
            nifty_option.key.token: "3:0",
            bank_option.key.token: "3:1",
            nifty_future.key.token: "3:2",
            bank_future.key.token: "3:2",
        }

        manager.unsubscribe("shared-options-owner", (bank_option.key,))
        assert manager._shard_assignments[3] == {
            (2, nifty_option.key.token): 0,
            (2, nifty_future.key.token): 2,
            (2, bank_future.key.token): 2,
        }
        assert set(manager._route_index) == {SocketGroup(3, 0), SocketGroup(3, 2)}
    finally:
        manager.close()



def test_locked_lane_rejects_token_count_above_per_socket_limit_without_route_mutation():
    registry = InstrumentRegistry()
    locked = [
        _descriptor(f"NIFTY-WEEKLY-CAP-{index:04d}")
        for index in range(1001)
    ]
    registry.register_many(locked)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    manager._max_tokens_per_socket = 1000

    try:
        try:
            manager.subscribe(
                "shared-index-market-data:options",
                tuple(item.key for item in locked),
                mode=3,
            )
        except ValueError as exc:
            assert "locked socket lane 0 exceeds token limit" in str(exc)
            assert "1001 instruments" in str(exc)
        else:
            raise AssertionError("locked lane must not exceed the per-socket token ceiling")

        # Subscription intent remains retryable, but no partial allocator,
        # route, or physical socket state may be committed.
        assert len(registry.subscriptions()) == 1001
        assert manager._shard_assignments == {}
        assert manager._route_index == {}
        assert manager._sockets == {}
    finally:
        manager.close()



def test_same_lane_broker_token_collision_between_descriptors_fails_closed():
    registry = InstrumentRegistry()
    locked = _descriptor("NIFTY-SAME-LANE-COLLISION")
    alias = InstrumentDescriptor(
        key=InstrumentKey("NFO", "DERIVATIVE", locked.key.token),
        symbol="NIFTY-ALIAS",
        instrument_type="option",
        exchange="NFO",
        segment="DERIVATIVE",
        underlying="NIFTY",
        expiry_type="WEEKLY",
        expiry="2026-12-31",
        strike=25000.0,
        option_type="CE",
    )
    registry.register_many((locked, alias))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        try:
            manager.subscribe("locked-owner", (locked.key,), mode=3)
            before_routes = {group: dict(routes) for group, routes in manager._route_index.items()}
            before_sockets = set(manager._sockets)
            manager.subscribe("alias-owner", (alias.key,), mode=3)
        except ValueError as exc:
            assert "ambiguous broker token affinity" in str(exc)
            assert "multiple instrument descriptors" in str(exc)
        else:
            raise AssertionError("one broker token must not fan out to distinct instrument descriptors")

        assert manager._route_index == before_routes
        assert set(manager._sockets) == before_sockets
        assert manager._shard_assignments[3] == {
            (2, locked.key.token): 0,
        }
    finally:
        manager.close()



def test_recovery_reconciles_stale_lane_plan_even_when_group_set_is_unchanged():
    registry = InstrumentRegistry()
    nifty = _descriptor("NIFTY-WEEKLY-RECOVERY")
    bank = _descriptor("BANK-MONTHLY-RECOVERY")

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=token,
            instrument_type="future",
            exchange="NFO",
            segment="NFO",
            underlying=underlying,
            expiry="2026-12-31",
        )

    nifty_future = future("NIFTY-FUT-RECOVERY", "NIFTY")
    bank_future = future("BANK-FUT-RECOVERY", "BANKNIFTY")
    registry.register_many((nifty, bank, nifty_future, bank_future))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    expected = {
        (2, nifty.key.token): 0,
        (2, bank.key.token): 1,
        (2, nifty_future.key.token): 2,
        (2, bank_future.key.token): 2,
    }
    try:
        manager.subscribe(
            "locked-options",
            (nifty.key, bank.key),
            mode=3,
        )
        manager.subscribe(
            "locked-futures",
            (nifty_future.key, bank_future.key),
            mode=3,
        )
        assert manager._shard_assignments[3] == expected
        assert set(manager._sockets) == {
            SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
        }

        # Simulate stale in-memory affinity metadata after a descriptor refresh.
        # The same three physical groups still exist, so recovery must compare
        # the token-to-lane plan, not only the set/count of socket groups.
        manager._shard_assignments[3] = {
            (2, nifty.key.token): 1,
            (2, bank.key.token): 0,
            (2, nifty_future.key.token): 2,
            (2, bank_future.key.token): 2,
        }
        assert set(manager._sockets) == {
            SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
        }

        assert manager.recover_disconnected(min_age_seconds=0.0) == 0
        assert manager._shard_assignments[3] == expected
        assert set(manager._route_index) == {
            SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
        }
        assert manager._socket_tokens[SocketGroup(3, 0)] == {(2, nifty.key.token)}
        assert manager._socket_tokens[SocketGroup(3, 1)] == {(2, bank.key.token)}
        assert manager._socket_tokens[SocketGroup(3, 2)] == {
            (2, nifty_future.key.token),
            (2, bank_future.key.token),
        }
    finally:
        manager.close()



def test_each_locked_lane_recovers_in_place_without_cross_lane_token_migration():
    registry = InstrumentRegistry()
    nifty_spot = InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "NIFTY-SPOT-INPLACE"),
        symbol="NIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="NIFTY",
    )
    bank_spot = InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "BANK-SPOT-INPLACE"),
        symbol="BANKNIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="BANKNIFTY",
    )
    nifty_option = _descriptor("NIFTY-WEEKLY-INPLACE")
    bank_option = _descriptor("BANK-MONTHLY-INPLACE")

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=token,
            instrument_type="future",
            exchange="NFO",
            segment="NFO",
            underlying=underlying,
            expiry="2026-12-31",
        )

    nifty_future = future("NIFTY-FUT-INPLACE", "NIFTY")
    bank_future = future("BANK-FUT-INPLACE", "BANKNIFTY")
    registry.register_many((
        nifty_spot, bank_spot, nifty_option, bank_option, nifty_future, bank_future,
    ))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    owners = (
        ("nifty-spot", (nifty_spot.key,)),
        ("bank-spot", (bank_spot.key,)),
        ("options", (nifty_option.key, bank_option.key)),
        ("futures", (nifty_future.key, bank_future.key)),
    )
    for owner, keys in owners:
        manager.register_callback(owner, lambda _message: None)

    try:
        for owner, keys in owners:
            manager.subscribe(owner, keys, mode=3)

        expected_assignments = {
            (1, nifty_spot.key.token): 0,
            (1, bank_spot.key.token): 1,
            (2, nifty_option.key.token): 0,
            (2, bank_option.key.token): 1,
            (2, nifty_future.key.token): 2,
            (2, bank_future.key.token): 2,
        }
        assert manager._shard_assignments[3] == expected_assignments
        original_sockets = dict(manager._sockets)

        for failed_group in (
            SocketGroup(3, 0),
            SocketGroup(3, 1),
            SocketGroup(3, 2),
        ):
            failed_socket = manager._sockets[failed_group]
            with manager._lock:
                manager._sockets.pop(failed_group)
                manager._socket_tokens.pop(failed_group, None)
                manager._socket_created_at.pop(failed_group, None)
                manager._last_data_at.pop(failed_group, None)
                manager._last_raw_frame_at_by_group.pop(failed_group, None)
                manager._last_successful_route_at.pop(failed_group, None)
                manager._last_successful_delivery_at.pop(failed_group, None)
            failed_socket.close()

            assert manager.recover_disconnected(min_age_seconds=0.0) == 0
            assert set(manager._sockets) == {
                SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2),
            }
            assert manager._sockets[failed_group] is not failed_socket
            for healthy_group in set(original_sockets) - {failed_group}:
                assert manager._sockets[healthy_group] is original_sockets[healthy_group]
            assert manager._shard_assignments[3] == expected_assignments
            assert manager._socket_tokens[SocketGroup(3, 0)] == {
                (1, nifty_spot.key.token),
                (2, nifty_option.key.token),
            }
            assert manager._socket_tokens[SocketGroup(3, 1)] == {
                (1, bank_spot.key.token),
                (2, bank_option.key.token),
            }
            assert manager._socket_tokens[SocketGroup(3, 2)] == {
                (2, nifty_future.key.token),
                (2, bank_future.key.token),
            }
            original_sockets[failed_group] = manager._sockets[failed_group]
    finally:
        manager.close()


def test_shared_instrument_mode_upgrade_and_downgrade_preserves_only_active_routes():
    """A shared key follows max consumer mode without duplicate physical ownership."""
    registry = InstrumentRegistry()
    item = _descriptor("NIFTY-SHARED-MODE-ROLL")
    registry.register(item)
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    delivered = []
    manager.register_callback("low-mode-owner", lambda message: delivered.append(("low", message["token"])))
    manager.register_callback("high-mode-owner", lambda message: delivered.append(("high", message["token"])))

    try:
        manager.subscribe("low-mode-owner", (item.key,), mode=1)
        low_group = SocketGroup(1, 0)
        low_socket = manager._sockets[low_group]
        assert manager.snapshot()["required_socket_groups"] == ["1:0"]

        manager.subscribe("high-mode-owner", (item.key,), mode=3)
        high_group = SocketGroup(3, 0)
        assert high_group in manager._sockets
        assert low_group not in manager._sockets
        assert low_socket.closed is True
        assert set(manager._route_index) == {high_group}
        assert set(manager._route_index[high_group][(2, item.key.token)]) == {
            ("high-mode-owner", item.key),
            ("low-mode-owner", item.key),
        }
        upgraded = manager.snapshot()
        assert upgraded["required_socket_groups"] == ["3:0"]
        assert upgraded["socket_token_set_mismatches"] == []
        manager._on_data(high_group, {
            "token": item.key.token,
            "exchange_type": 2,
            "ltp": 12300,
        })
        assert set(delivered) == {
            ("low", item.key.token),
            ("high", item.key.token),
        }
        delivered.clear()

        manager.unsubscribe("high-mode-owner", (item.key,))
        assert low_group in manager._sockets
        assert high_group not in manager._sockets
        downgraded = manager.snapshot()
        assert downgraded["required_socket_groups"] == ["1:0"]
        assert downgraded["socket_token_set_mismatches"] == []
        assert downgraded["subscriptions"] == 1

        manager._on_data(low_group, {
            "token": item.key.token,
            "exchange_type": 2,
            "ltp": 12345,
        })
        assert delivered == [("low", item.key.token)]
    finally:
        manager.close()


def test_locked_three_lane_plan_rejects_additional_mode_group_atomically():
    """A non-locked mode cannot consume a fourth physical session beside locked lanes."""
    registry = InstrumentRegistry()
    nifty = _descriptor("NIFTY-WEEKLY-MODE-BUDGET")
    bank = _descriptor("BANK-MONTHLY-MODE-BUDGET")

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=token,
            instrument_type="future",
            exchange="NFO",
            segment="NFO",
            underlying=underlying,
            expiry="2026-12-31",
        )

    nifty_future = future("NIFTY-FUT-MODE-BUDGET", "NIFTY")
    bank_future = future("BANK-FUT-MODE-BUDGET", "BANKNIFTY")
    other_mode = _descriptor("OTHER-MODE-ONE-BUDGET")
    registry.register_many((nifty, bank, nifty_future, bank_future, other_mode))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("locked-options", (nifty.key, bank.key), mode=3)
        manager.subscribe("locked-futures", (nifty_future.key, bank_future.key), mode=3)
        expected_groups = {
            SocketGroup(3, 0),
            SocketGroup(3, 1),
            SocketGroup(3, 2),
        }
        assert set(manager._sockets) == expected_groups
        before_assignments = {
            mode: dict(assignments)
            for mode, assignments in manager._shard_assignments.items()
        }
        before_routes = {
            group: dict(routes)
            for group, routes in manager._route_index.items()
        }
        before_sockets = dict(manager._sockets)

        try:
            manager.subscribe("mode-one-adaptive-owner", (other_mode.key,), mode=1)
        except ValueError as exc:
            assert "global WebSocket limit exceeded" in str(exc)
            assert "4 sessions required" in str(exc)
        else:
            raise AssertionError("mode-1 adaptive feed must not exceed the three-session global cap")

        assert manager._shard_assignments == before_assignments
        assert manager._route_index == before_routes
        assert manager._sockets == before_sockets
        assert set(manager._sockets) == expected_groups
        assert manager.snapshot()["socket_groups"] == 3
    finally:
        manager.close()



def test_simultaneous_locked_lane_failures_invalidate_and_retire_each_lane_independently():
    from threading import Event, Thread
    import time

    registry = InstrumentRegistry()
    nifty_spot = InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "NIFTY-SPOT-SIMULTANEOUS"),
        symbol="NIFTY", instrument_type="index", exchange="NSE", segment="NSE",
        underlying="NIFTY",
    )
    bank_spot = InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "BANK-SPOT-SIMULTANEOUS"),
        symbol="BANKNIFTY", instrument_type="index", exchange="NSE", segment="NSE",
        underlying="BANKNIFTY",
    )
    nifty_option = _descriptor("NIFTY-WEEKLY-SIMULTANEOUS")
    bank_option = _descriptor("BANK-MONTHLY-SIMULTANEOUS")

    def future(token, underlying):
        return InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token), symbol=token,
            instrument_type="future", exchange="NFO", segment="NFO",
            underlying=underlying, expiry="2026-12-31",
        )

    nifty_future = future("NIFTY-FUT-SIMULTANEOUS", "NIFTY")
    bank_future = future("BANK-FUT-SIMULTANEOUS", "BANKNIFTY")
    registry.register_many((
        nifty_spot, bank_spot, nifty_option, bank_option, nifty_future, bank_future,
    ))
    manager = CommonWebSocketManager(registry, socket_factory=RouteSocket)
    manager._recovery_interval_seconds = 60.0
    for owner in ("nifty-spot", "bank-spot", "options", "futures"):
        manager.register_callback(owner, lambda _message: None)

    groups = (SocketGroup(3, 0), SocketGroup(3, 1), SocketGroup(3, 2))
    callbacks = {}
    callbacks_ready = Event()
    start = Event()
    try:
        manager.subscribe("nifty-spot", (nifty_spot.key,), mode=3)
        manager.subscribe("bank-spot", (bank_spot.key,), mode=3)
        manager.subscribe("options", (nifty_option.key, bank_option.key), mode=3)
        manager.subscribe("futures", (nifty_future.key, bank_future.key), mode=3)
        original_assignments = dict(manager._shard_assignments[3])
        original_routes = {group: dict(routes) for group, routes in manager._route_index.items()}
        failed_sockets = {group: manager._sockets[group] for group in groups}

        def defer_close(socket, **kwargs):
            callbacks[kwargs["group"]] = kwargs.get("on_finished")
            if len(callbacks) == 3:
                callbacks_ready.set()
            return Thread()

        manager._close_socket_tracked = defer_close

        def fail(group):
            assert start.wait(timeout=1.0)
            manager._handle_socket_failure(
                group, failed_sockets[group], manager._socket_generation[group],
                "simultaneous deterministic lane failure",
            )

        workers = [Thread(target=fail, args=(group,), daemon=True) for group in groups]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(timeout=1.0)
            assert not worker.is_alive(), "lane failure callback must remain bounded"

        assert callbacks_ready.wait(timeout=1.0), "every failed physical lane must be retired"
        assert set(callbacks) == set(groups)
        assert manager._sockets == {}
        assert manager._failure_recovery_groups == set(groups)
        assert manager.snapshot()["failure_recovery_admission_rejections"] == 2
        assert manager._shard_assignments[3] == original_assignments
        assert manager._route_index == original_routes
        assert set(manager._failure_recovery_pending_groups) == set(groups) - {
            next(iter(manager._failure_recovery_groups - manager._failure_recovery_pending_groups))
        }
    finally:
        manager.close()
