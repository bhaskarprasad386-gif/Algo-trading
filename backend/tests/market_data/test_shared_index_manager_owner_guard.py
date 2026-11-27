import pytest

from app.market_data.common_strategy_feed import (
    CommonStrategyMarketFeed,
    SharedIndexOnlyWebSocketManager,
)
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


def _descriptor():
    return InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "99001"),
        symbol="NIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="NIFTY",
    )


def test_production_manager_accepts_only_shared_index_runner_consumers():
    manager = SharedIndexOnlyWebSocketManager(socket_factory=lambda: object())

    assert manager.validate_consumer("shared-index-market-data:spot")
    assert manager.validate_consumer("shared-index-market-data:options")
    assert manager.validate_consumer("shared-index-market-data:futures")

    for consumer in (
        "box-underlyings",
        "box-options",
        "box-spread-live-depth-shadow",
        "iv-skew-flip-spot",
        "cash-future-live",
        "calendar-spread",
        "synthetic-options",
        "commodity-market-data",
    ):
        with pytest.raises(PermissionError, match="exclusively"):
            manager.validate_consumer(consumer)


def test_rejected_strategy_feed_does_not_mutate_registry_or_callback_routes():
    manager = SharedIndexOnlyWebSocketManager(socket_factory=lambda: object())
    feed = CommonStrategyMarketFeed("box-underlyings", manager=manager)

    with pytest.raises(PermissionError, match="exclusively"):
        feed.start((_descriptor(),), lambda _record: None)

    assert manager.registry.get(_descriptor().key) is None
    assert manager.registry.subscriptions() == ()
    assert manager._record_callbacks == {}


def test_direct_non_runner_subscription_is_rejected_before_registry_mutation():
    manager = SharedIndexOnlyWebSocketManager(socket_factory=lambda: object())
    descriptor = _descriptor()
    manager.registry.register(descriptor)

    with pytest.raises(PermissionError, match="exclusively"):
        manager.subscribe("iv-skew-flip-spot", [descriptor.key], mode=3)

    assert manager.registry.subscriptions() == ()


def test_shared_runner_owner_cannot_subscribe_outside_locked_index_universe():
    manager = SharedIndexOnlyWebSocketManager(socket_factory=lambda: object())
    descriptor = InstrumentDescriptor(
        key=InstrumentKey("BSE", "BSE", "88001"),
        symbol="SENSEX",
        instrument_type="index",
        exchange="BSE",
        segment="BSE",
        underlying="SENSEX",
    )
    feed = CommonStrategyMarketFeed(
        "shared-index-market-data:spot",
        manager=manager,
    )

    with pytest.raises(PermissionError, match="NIFTY/BANKNIFTY"):
        feed.start((descriptor,), lambda _record: None)

    assert manager.registry.get(descriptor.key) is None
    assert manager.registry.subscriptions() == ()



def test_non_runner_cannot_register_or_unregister_callback_routes():
    manager = SharedIndexOnlyWebSocketManager(socket_factory=lambda: object())
    blocked = (
        manager.register_callback,
        manager.unregister_callback,
        manager.register_normalized_callback,
        manager.unregister_normalized_callback,
        manager.register_normalized_observer,
        manager.unregister_normalized_observer,
        manager.register_transport_observer,
        manager.unregister_transport_observer,
    )
    for operation in blocked:
        with pytest.raises(PermissionError, match="exclusively"):
            if operation in {manager.register_callback, manager.register_normalized_callback,
                             manager.register_normalized_observer, manager.register_transport_observer}:
                operation("cash-future-live", lambda *_args, **_kwargs: None)
            else:
                operation("cash-future-live")
    assert manager._callbacks == {}
    assert manager._record_callbacks == {}
    assert manager._record_observers == {}
    assert manager._transport_observers == {}
