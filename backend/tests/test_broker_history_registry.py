import pytest

from app.brokers.history_registry import BrokerHistoryProviderRegistry


class _HistoryProvider:
    def __init__(self, name="angel_one"):
        self.name = name

    def fetch_order_trade_snapshot(self, *, account_id, window_start, window_end):
        raise AssertionError("registry must not invoke provider methods")


def test_history_registry_normalizes_names_and_keeps_provider_identity():
    registry = BrokerHistoryProviderRegistry()
    provider = _HistoryProvider("  ANGEL_ONE  ")

    registry.register(provider)

    assert registry.names() == ("angel_one",)
    assert registry.get(" ANGEL_ONE ") is provider


def test_history_registry_rejects_duplicate_provider_names():
    registry = BrokerHistoryProviderRegistry()
    registry.register(_HistoryProvider("zerodha"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_HistoryProvider(" ZERODHA "))


def test_history_registry_rejects_empty_provider_name():
    with pytest.raises(ValueError, match="name cannot be empty"):
        BrokerHistoryProviderRegistry().register(_HistoryProvider("  "))


def test_history_registry_rejects_objects_without_read_only_history_method():
    class InvalidProvider:
        name = "fyers"

    with pytest.raises(TypeError, match="fetch_order_trade_snapshot"):
        BrokerHistoryProviderRegistry().register(InvalidProvider())


def test_history_registry_does_not_fall_back_to_trading_registry():
    registry = BrokerHistoryProviderRegistry()
    with pytest.raises(KeyError, match="unsupported broker history provider"):
        registry.get("unknown")
