import pytest

from app.execution.fill_pnl import ExecutedFill, FillPnlState, apply_executed_fill


def test_only_executed_fills_change_realized_pnl():
    state = FillPnlState()
    state = apply_executed_fill(state, ExecutedFill("BUY", 100.0, 10, "f1"))
    assert state.quantity == 10
    assert state.realized_pnl == 0

    state = apply_executed_fill(state, ExecutedFill("SELL", 110.0, 4, "f2"))
    assert state.quantity == 6
    assert state.realized_pnl == 40.0


def test_partial_exit_keeps_average_price_and_final_exit_resets_position():
    state = apply_executed_fill(FillPnlState(), ExecutedFill("BUY", 100.0, 5))
    state = apply_executed_fill(state, ExecutedFill("BUY", 110.0, 5))
    assert state.average_price == 105.0

    state = apply_executed_fill(state, ExecutedFill("SELL", 115.0, 5))
    assert state.quantity == 5
    assert state.average_price == 105.0
    assert state.realized_pnl == 50.0

    state = apply_executed_fill(state, ExecutedFill("SELL", 95.0, 5))
    assert state.quantity == 0
    assert state.average_price == 0.0
    assert state.realized_pnl == 0.0


def test_sell_cannot_exceed_executed_position():
    state = apply_executed_fill(FillPnlState(), ExecutedFill("BUY", 100.0, 2))
    with pytest.raises(ValueError, match="exceeds executed long quantity"):
        apply_executed_fill(state, ExecutedFill("SELL", 101.0, 3))


def test_invalid_fill_is_rejected():
    with pytest.raises(ValueError):
        ExecutedFill("BUY", 0, 1)
    with pytest.raises(ValueError):
        ExecutedFill("HOLD", 100, 1)
