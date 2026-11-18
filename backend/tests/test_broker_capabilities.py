"""Batch 0 capability matrix must never imply unsupported execution rights."""

import pytest

from app.brokers.capabilities import BrokerCapabilityRegistry


EXPECTED = {
    "angel_one": "CURRENT_IST_TRADING_DAY",
    "zerodha": "CURRENT_IST_TRADING_DAY",
    "fyers": "CLOSED_IST_CALENDAR_DAYS",
    "shoonya": "CURRENT_IST_TRADING_DAY",
    "mstock": "CURRENT_IST_TRADING_DAY",
}


def test_all_five_brokers_have_explicit_read_only_history_capabilities():
    registry = BrokerCapabilityRegistry()
    capabilities = {item.broker: item for item in registry.all()}

    assert set(capabilities) == set(EXPECTED)
    for broker, history_window in EXPECTED.items():
        capability = capabilities[broker]
        assert capability.history_provider is True
        assert capability.history_window == history_window
        assert capability.history_limitations.strip()
        assert capability.can_place_orders is False
        assert capability.can_cancel_orders is False
        assert capability.can_modify_orders is False
        assert capability.live_certified is False


def test_only_angel_one_is_registered_in_the_shared_routing_registry():
    capabilities = {item.broker: item for item in BrokerCapabilityRegistry().all()}
    assert capabilities["angel_one"].adapter_registered_for_routing is True
    for broker in {"zerodha", "fyers", "shoonya", "mstock"}:
        assert capabilities[broker].adapter_registered_for_routing is False


@pytest.mark.parametrize("name", ["", "unknown", "angelone", "zerodha_live"])
def test_unknown_broker_capability_fails_closed(name):
    with pytest.raises(KeyError):
        BrokerCapabilityRegistry().get(name)
