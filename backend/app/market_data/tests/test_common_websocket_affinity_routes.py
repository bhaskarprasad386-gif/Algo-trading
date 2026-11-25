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
    return InstrumentDescriptor(
        key=InstrumentKey(exchange, exchange, token),
        symbol=f"OPT-{token}",
        instrument_type="option",
        exchange=exchange,
        segment=exchange,
        underlying="NIFTY" if token.endswith("W") else "BANKNIFTY",
        expiry_type="WEEKLY" if token.endswith("W") else "MONTHLY",
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
