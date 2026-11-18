import pytest

from app.backtesting.execution import ExecutionSide, OrderType, SimOrder
from app.backtesting.linked_orders import LinkedOrderManager, TrailingSpec


def test_bracket_children_activate_only_after_parent_fill():
    manager = LinkedOrderManager()
    parent = SimOrder("P1", "NSE:SBIN", ExecutionSide.BUY, 10)
    tp = SimOrder("TP1", "NSE:SBIN", ExecutionSide.SELL, 10, OrderType.LIMIT, limit_price=110.0)
    sl = SimOrder("SL1", "NSE:SBIN", ExecutionSide.SELL, 10, OrderType.STOP, stop_price=95.0)

    manager.register_bracket("B1", parent, tp, sl)
    assert manager.active_orders("B1") == ()

    children = manager.activate_bracket("B1", 10, 1_000)
    assert {o.order_id for o in children} == {"TP1", "SL1"}
    assert {o.order_id for o in manager.active_orders("B1")} == {"TP1", "SL1"}


def test_oco_first_child_fill_cancels_the_other_child():
    manager = LinkedOrderManager()
    a = SimOrder("O1", "NSE:SBIN", ExecutionSide.SELL, 10, OrderType.LIMIT, limit_price=110.0)
    b = SimOrder("O2", "NSE:SBIN", ExecutionSide.SELL, 10, OrderType.STOP, stop_price=95.0)
    manager.register_oco("OCO1", (a, b))

    cancelled = manager.on_fill("OCO1", "O1", 2_000)
    assert cancelled == ("O2",)
    assert manager.active_orders("OCO1") == (a,)


def test_trailing_sell_stop_only_moves_up_with_new_highs():
    manager = LinkedOrderManager()
    order = SimOrder("T1", "NSE:SBIN", ExecutionSide.SELL, 10, OrderType.STOP, stop_price=95.0)
    manager.register_trailing("T1", order, TrailingSpec(5.0))

    first = manager.update_trailing("T1", 100.0, 1_000)
    second = manager.update_trailing("T1", 103.0, 2_000)
    third = manager.update_trailing("T1", 101.0, 3_000)

    assert first.stop_price == pytest.approx(95.0)
    assert second.stop_price == pytest.approx(98.0)
    assert third.stop_price == pytest.approx(98.0)


def test_trailing_buy_stop_only_moves_down_with_new_lows_and_supports_percent():
    manager = LinkedOrderManager()
    order = SimOrder("T2", "NSE:SBIN", ExecutionSide.BUY, 10, OrderType.STOP, stop_price=105.0)
    manager.register_trailing("T2", order, TrailingSpec(10.0, percent=True))

    first = manager.update_trailing("T2", 100.0, 1_000)
    second = manager.update_trailing("T2", 97.0, 2_000)
    third = manager.update_trailing("T2", 99.0, 3_000)

    assert first.stop_price == pytest.approx(110.0)
    assert second.stop_price == pytest.approx(106.7)
    assert third.stop_price == pytest.approx(106.7)


def test_linked_order_validation_is_conservative():
    manager = LinkedOrderManager()
    parent = SimOrder("P2", "NSE:SBIN", ExecutionSide.BUY, 1)
    tp = SimOrder("TP2", "NSE:SBIN", ExecutionSide.SELL, 1, OrderType.LIMIT, limit_price=110.0)
    sl = SimOrder("SL2", "NSE:NIFTY", ExecutionSide.SELL, 1, OrderType.STOP, stop_price=95.0)
    with pytest.raises(ValueError, match="instrument"):
        manager.register_bracket("B2", parent, tp, sl)
