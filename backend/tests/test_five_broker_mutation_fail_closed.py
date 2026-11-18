"""Cross-broker guard: history support must never imply live order authority."""

import pytest

from app.brokers.angel_one import AngelOneAdapter
from app.brokers.fyers import FyersAdapter
from app.brokers.mstock import MStockAdapter
from app.brokers.registry import BrokerRegistry
from app.brokers.shoonya import ShoonyaAdapter
from app.brokers.zerodha import ZerodhaAdapter


@pytest.fixture
def read_only_adapters():
    # Inject inert clients. These tests must never authenticate or access a network.
    return (
        AngelOneAdapter(),
        ZerodhaAdapter(object()),
        FyersAdapter(object()),
        ShoonyaAdapter(object()),
        MStockAdapter(http=object()),
    )


@pytest.mark.parametrize("operation", ["place_order", "cancel_order"])
def test_all_five_brokers_fail_closed_for_order_mutations(read_only_adapters, operation):
    for adapter in read_only_adapters:
        with pytest.raises(NotImplementedError):
            getattr(adapter, operation)()


def test_no_history_adapter_exposes_order_modification(read_only_adapters):
    for adapter in read_only_adapters:
        assert not hasattr(adapter, "modify_order"), adapter.name


def test_history_provider_work_does_not_register_unreviewed_live_adapters():
    # Registration is an execution capability boundary, not a list of history
    # providers. Keep it narrow until each broker's full execution lifecycle,
    # fill persistence, reconciliation and recovery controls are reviewed.
    registry = BrokerRegistry()
    assert registry.names() == ("angel_one",)
