from app.execution.fill_accounting import ExecutedFill, FillAccountingState, apply_executed_fill


def test_long_entry_and_partial_exit_realize_only_executed_quantity():
    state = apply_executed_fill(FillAccountingState(), ExecutedFill("BUY", 100, 10, "b1"))
    state = apply_executed_fill(state, ExecutedFill("SELL", 110, 4, "s1"))
    assert state.quantity == 6
    assert state.average_price == 100
    assert state.realized_pnl == 40


def test_short_entry_and_cover_realize_profit():
    state = apply_executed_fill(FillAccountingState(), ExecutedFill("SELL", 120, 5, "s1"))
    state = apply_executed_fill(state, ExecutedFill("BUY", 110, 5, "b1"))
    assert state.quantity == 0
    assert state.average_price == 0
    assert state.realized_pnl == 50


def test_reversal_opens_remainder_at_crossing_fill_price():
    state = apply_executed_fill(FillAccountingState(), ExecutedFill("BUY", 100, 5))
    state = apply_executed_fill(state, ExecutedFill("SELL", 110, 8))
    assert state.quantity == -3
    assert state.average_price == 110
    assert state.realized_pnl == 50


def test_invalid_fill_is_rejected():
    try:
        ExecutedFill("HOLD", 100, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid fill side must be rejected")
