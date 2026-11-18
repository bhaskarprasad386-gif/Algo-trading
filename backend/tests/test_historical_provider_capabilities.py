import pytest

from app.backtesting.historical_provider_capabilities import capabilities


def test_provider_capabilities_accept_only_declared_native_timeframes():
    caps = capabilities("angelone", ("1m", "3m", "5m", "1d"))

    assert caps.supports("1m")
    assert caps.supports("1d")
    assert not caps.supports("1ms")

    with pytest.raises(ValueError, match="does not natively support timeframe"):
        caps.require("1ms")


def test_provider_capabilities_are_deduplicated_and_sorted_for_errors():
    caps = capabilities("example", ("5m", "1m", "5m"))

    assert caps.timeframes == frozenset({"1m", "5m"})
    with pytest.raises(ValueError, match="1m, 5m"):
        caps.require("1d")


def test_provider_can_declare_genuine_high_resolution_data():
    caps = capabilities("high_resolution_provider", ("1ms", "1us", "tick"))

    assert caps.supports("1ms")
    assert caps.supports("1us")
    assert caps.supports("tick")

    caps.require("1ms")
    caps.require("1us")
    caps.require("tick")
