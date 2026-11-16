"""Composition tests for the five read-only broker history providers."""

import pytest

from app.brokers.history_registry import BrokerHistoryProviderRegistry
from app.brokers.history import BrokerHistorySnapshotProvider


@pytest.mark.parametrize("broker", ["angel_one", "zerodha", "fyers", "shoonya", "mstock"])
def test_registry_builds_only_matching_read_only_history_provider(broker):
    class SessionAdapter:
        name = broker

    provider = BrokerHistoryProviderRegistry().build(broker, SessionAdapter())
    assert isinstance(provider, BrokerHistorySnapshotProvider)
    assert provider.name == broker
    assert callable(provider.fetch_order_trade_snapshot)
    assert not hasattr(provider, "place_order")
    assert not hasattr(provider, "cancel_order")
    assert not hasattr(provider, "modify_order")


def test_registry_rejects_cross_broker_session_adapter():
    class SessionAdapter:
        name = "angel_one"

    with pytest.raises(ValueError, match="identity mismatch"):
        BrokerHistoryProviderRegistry().build("zerodha", SessionAdapter())


@pytest.mark.parametrize("broker", ["", "unknown", "angelone"])
def test_registry_rejects_unknown_provider(broker):
    class SessionAdapter:
        name = broker

    with pytest.raises(KeyError):
        BrokerHistoryProviderRegistry().build(broker, SessionAdapter())


def test_registry_names_are_complete_but_do_not_expand_order_routing_registry():
    assert BrokerHistoryProviderRegistry().names() == (
        "angel_one", "fyers", "mstock", "shoonya", "zerodha",
    )
