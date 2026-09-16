import math

import pytest

from app.backtesting.execution import ExecutionSide
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus


def _state(average_fill_price):
    return {
        "order": {
            "order_id": "o1",
            "instrument": "SBIN",
            "side": ExecutionSide.BUY.value,
            "quantity": 1,
            "order_type": "MARKET",
            "submitted_at_ns": 100,
            "queue_ahead_quantity": 0,
            "time_in_force": "DAY",
        },
        "status": OrderStatus.FILLED.value,
        "filled_quantity": 1,
        "average_fill_price": average_fill_price,
        "reject_reason": None,
        "time_in_force": "DAY",
        "events": [],
    }


def test_restore_state_rejects_non_finite_average_fill_price():
    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError, match="invalid lifecycle average fill price"):
            OrderLifecycle.restore_state(_state(value))


def test_restore_state_rejects_non_numeric_average_fill_price():
    for value in ("100.0", "nan", "inf", None):
        with pytest.raises(ValueError, match="invalid lifecycle average fill price"):
            OrderLifecycle.restore_state(_state(value))


def test_restore_state_accepts_finite_average_fill_price():
    lifecycle = OrderLifecycle.restore_state(_state(100.0))
    assert lifecycle.state.status is OrderStatus.FILLED
    assert lifecycle.state.average_fill_price == 100.0
