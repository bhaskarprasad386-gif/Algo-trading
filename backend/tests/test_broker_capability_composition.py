"""Cross-registry Batch 0 capability invariants for all supported brokers.

History-provider composition must never be mistaken for order-routing approval.
"""
import pytest

from app.brokers.capabilities import broker_capabilities
from app.brokers.history_registry import broker_history_providers
from app.brokers.registry import BrokerRegistry


EXPECTED_BROKERS = ("angel_one", "fyers", "mstock", "shoonya", "zerodha")


def test_five_broker_history_registry_and_capability_catalogue_stay_in_sync():
    assert broker_history_providers.names() == EXPECTED_BROKERS
    capabilities = {item.broker: item for item in broker_capabilities.all()}
    assert tuple(sorted(capabilities)) == EXPECTED_BROKERS
    assert all(item.history_provider for item in capabilities.values())


def test_history_composition_is_read_only_and_never_grants_order_mutation():
    for broker in EXPECTED_BROKERS:
        class SessionAdapter:
            name = broker

        provider = broker_history_providers.build(broker, SessionAdapter())
        assert provider.name == broker
        assert callable(provider.fetch_order_trade_snapshot)
        assert not hasattr(provider, "place_order")
        assert not hasattr(provider, "cancel_order")
        assert not hasattr(provider, "modify_order")


def test_routing_registry_and_capability_flags_report_only_real_registered_adapters():
    routing_names = BrokerRegistry().names()
    capabilities = {item.broker: item for item in broker_capabilities.all()}

    assert routing_names == ("angel_one",)
    for broker, capability in capabilities.items():
        assert capability.adapter_registered_for_routing is (broker in routing_names)
        assert capability.can_place_orders is False
        assert capability.can_cancel_orders is False
        assert capability.can_modify_orders is False
        assert capability.live_certified is False


@pytest.mark.parametrize("broker", ["", "unknown", "angelone"])
def test_unknown_history_provider_cannot_be_composed(broker):
    class SessionAdapter:
        name = broker

    with pytest.raises(KeyError):
        broker_history_providers.build(broker, SessionAdapter())
