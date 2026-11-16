"""Cross-provider structural conformance for the read-only history capability."""

import inspect

import pytest

from app.brokers.angel_one_history import AngelOneHistoryProvider
from app.brokers.fyers_history import FyersHistoryProvider
from app.brokers.history import BrokerHistorySnapshotProvider
from app.brokers.mstock_history import MStockHistoryProvider
from app.brokers.shoonya_history import ShoonyaHistoryProvider
from app.brokers.zerodha_history import ZerodhaHistoryProvider


PROVIDERS = (
    (AngelOneHistoryProvider, "angel_one"),
    (ZerodhaHistoryProvider, "zerodha"),
    (FyersHistoryProvider, "fyers"),
    (ShoonyaHistoryProvider, "shoonya"),
    (MStockHistoryProvider, "mstock"),
)


@pytest.mark.parametrize(("provider_type", "expected_name"), PROVIDERS)
def test_all_five_history_providers_implement_the_same_read_only_contract(
    provider_type, expected_name
):
    method = getattr(provider_type, "fetch_order_trade_snapshot", None)
    assert callable(method), provider_type.__name__

    signature = inspect.signature(method)
    assert tuple(signature.parameters) == (
        "self", "account_id", "window_start", "window_end",
    )
    assert provider_type.name == expected_name

    # The protocol is intentionally a read-only history capability, not an
    # order execution interface. Adapter mutation remains separately gated.
    assert not hasattr(provider_type, "place_order")
    assert not hasattr(provider_type, "cancel_order")
    assert not hasattr(provider_type, "modify_order")


def test_provider_protocol_exposes_only_history_snapshot_operation():
    protocol_methods = {
        name for name, value in vars(BrokerHistorySnapshotProvider).items()
        if callable(value) and not name.startswith("_")
    }
    assert protocol_methods == {"fetch_order_trade_snapshot"}
