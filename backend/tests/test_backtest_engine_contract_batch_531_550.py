from datetime import datetime, timezone

import pytest

from app.algo.strategy import Strategy, StrategyRule, threshold_rule
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _strategies():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    return entry, exit_


def test_quantity_step_rejects_non_lot_multiple():
    with pytest.raises(ValueError, match="exact multiple"):
        BacktestConfig(quantity=1, quantity_step=75)


def test_quantity_step_accepts_lot_multiple():
    assert BacktestConfig(quantity=150, quantity_step=75).quantity == 150


def test_tick_size_rounds_buy_and_sell_executions():
    entry, exit_ = _strategies()
    result = BacktestEngine(BacktestConfig(tick_size=0.05, slippage_rate=0.001)).run(
        [{"timestamp": 1, "close": 100.01, "signal": 1}, {"timestamp": 2, "close": 101.01, "signal": 0}], entry, exit_
    )
    assert result.trades[0].entry_price == 100.1
    assert result.trades[0].exit_price == 100.9


def test_minimum_transaction_cost_is_applied():
    entry, exit_ = _strategies()
    result = BacktestEngine(BacktestConfig(transaction_cost_rate=0.0, transaction_cost_minimum=25)).run(
        [{"timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 2, "close": 101, "signal": 0}], entry, exit_
    )
    assert result.trades[0].costs == 25


def test_nonfinite_ohlc_is_rejected():
    entry, exit_ = _strategies()
    for field in ("open", "high", "low"):
        with pytest.raises(ValueError, match=field):
            BacktestEngine().run([{ "timestamp": 1, "close": 100, field: float("nan"), "signal": 1}], entry, exit_)


def test_nonpositive_ohlc_is_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="high"):
        BacktestEngine().run([{ "timestamp": 1, "close": 100, "high": 0, "signal": 1}], entry, exit_)


def test_missing_close_is_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="close is required"):
        BacktestEngine().run([{ "timestamp": 1, "signal": 1}], entry, exit_)


def test_invalid_timestamp_type_is_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="timestamp"):
        BacktestEngine().run([{ "timestamp": "2026-01-01", "close": 100, "signal": 1}], entry, exit_)


def test_nonfinite_numeric_timestamp_is_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="timestamp"):
        BacktestEngine().run([{ "timestamp": float("inf"), "close": 100, "signal": 1}], entry, exit_)


def test_duplicate_timestamps_are_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="strictly increasing"):
        BacktestEngine().run([{ "timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 1, "close": 101, "signal": 0}], entry, exit_)


def test_mixed_timestamp_types_are_rejected():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="comparable"):
        BacktestEngine().run([{ "timestamp": 1, "close": 100, "signal": 1}, {"timestamp": datetime.now(timezone.utc), "close": 101, "signal": 0}], entry, exit_)


def test_integer_candle_fields_remain_in_strategy_context():
    seen = []
    class Capture:
        def evaluate(self, context):
            seen.append(context["token"])
            return False
    exit_ = Capture()
    BacktestEngine().run([{ "timestamp": 1, "close": 100, "token": 123}], Capture(), exit_)
    assert seen == [123]


def test_boolean_candle_values_are_excluded_from_numeric_context():
    seen = []
    class Capture:
        def evaluate(self, context):
            seen.append("flag" in context)
            return False
    BacktestEngine().run([{ "timestamp": 1, "close": 100, "flag": True}], Capture(), Capture())
    assert seen == [False, False]


def test_low_missing_is_allowed_for_close_execution_but_not_used_as_risk_mark():
    entry, exit_ = _strategies()
    result = BacktestEngine().run([{ "timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 2, "close": 101, "signal": 0}], entry, exit_)
    assert len(result.trades) == 1


def test_next_open_execution_uses_next_bar_open():
    entry, exit_ = _strategies()
    result = BacktestEngine(BacktestConfig(execution_timing="next_open")).run(
        [
            {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "signal": 1},
            {"timestamp": 2, "open": 103, "high": 104, "low": 102, "close": 103, "signal": 0},
            {"timestamp": 3, "open": 105, "high": 106, "low": 104, "close": 105, "signal": 0},
        ], entry, exit_
    )
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 103
    assert result.trades[0].exit_price == 105


def test_next_open_requires_open_field():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="open"):
        BacktestEngine(BacktestConfig(execution_timing="next_open")).run(
            [{"timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 2, "close": 101, "signal": 0}], entry, exit_
        )


def test_asymmetric_entry_exit_slippage_is_supported():
    entry, exit_ = _strategies()
    result = BacktestEngine(BacktestConfig(entry_slippage_rate=0.01, exit_slippage_rate=0.02)).run(
        [{"timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 2, "close": 110, "signal": 0}], entry, exit_
    )
    assert result.trades[0].entry_price == 101
    assert result.trades[0].exit_price == pytest.approx(107.8)


def test_cash_check_rejects_unaffordable_entry():
    entry, exit_ = _strategies()
    with pytest.raises(ValueError, match="insufficient cash"):
        BacktestEngine(BacktestConfig(initial_capital=100, quantity=2)).run(
            [{"timestamp": 1, "close": 60, "signal": 1}], entry, exit_
        )


def test_cash_check_can_be_disabled_for_margin_models():
    entry, exit_ = _strategies()
    result = BacktestEngine(BacktestConfig(initial_capital=100, quantity=2, enforce_cash=False)).run(
        [{"timestamp": 1, "close": 60, "signal": 1}, {"timestamp": 2, "close": 61, "signal": 0}], entry, exit_
    )
    assert result.net_pnl == 2


def test_close_execution_keeps_existing_behavior_by_default():
    entry, exit_ = _strategies()
    result = BacktestEngine().run(
        [{"timestamp": 1, "close": 100, "signal": 1}, {"timestamp": 2, "close": 110, "signal": 0}], entry, exit_
    )
    assert result.trades[0].entry_price == 100
    assert result.trades[0].exit_price == 110
