import pytest

from app.execution.limit_protection import (
    OrderSide,
    SlippageConfig,
    protected_limit_price,
    within_slippage,
)


def test_buy_limit_never_exceeds_slippage_cap():
    config = SlippageConfig(max_slippage_pct=0.01, tick_size=0.05)
    assert protected_limit_price(100, OrderSide.BUY, config) == 101.0


def test_sell_limit_never_goes_below_slippage_floor():
    config = SlippageConfig(max_slippage_pct=0.01, tick_size=0.05)
    assert protected_limit_price(100, OrderSide.SELL, config) == 99.0


def test_buy_rounds_down_to_tick():
    config = SlippageConfig(max_slippage_pct=0.003, tick_size=0.05)
    assert protected_limit_price(100, OrderSide.BUY, config) == 100.25


def test_sell_rounds_up_to_tick():
    config = SlippageConfig(max_slippage_pct=0.003, tick_size=0.05)
    assert protected_limit_price(100, OrderSide.SELL, config) == 99.75


def test_within_slippage_checks_both_sides():
    assert within_slippage(100, 100.5, OrderSide.BUY, 0.005)
    assert not within_slippage(100, 100.51, OrderSide.BUY, 0.005)
    assert within_slippage(100, 99.5, OrderSide.SELL, 0.005)
    assert not within_slippage(100, 99.49, OrderSide.SELL, 0.005)


def test_invalid_values_are_rejected():
    with pytest.raises(ValueError):
        SlippageConfig(max_slippage_pct=-0.01)
    with pytest.raises(ValueError):
        SlippageConfig(tick_size=0)
    with pytest.raises(ValueError):
        protected_limit_price(0, OrderSide.BUY)
    with pytest.raises(ValueError):
        within_slippage(100, 100, OrderSide.BUY, -0.01)
