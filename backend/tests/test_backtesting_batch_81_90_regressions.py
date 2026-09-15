import pytest

from app.backtesting.cash_future import CashFutureObservation, backtest_cash_future_basis, build_cash_future_observations
from app.backtesting.calendar_spread import CalendarQuote
from app.backtesting.arbitrage_strategy_adapters import CalendarSpreadStrategyAdapter
from app.backtesting.arbitrage_backtester import LiquidityPolicy


def test_cash_future_rejects_boolean_timestamp():
    with pytest.raises(ValueError):
        CashFutureObservation(True, 100.0, 105.0)


def test_cash_future_rejects_fractional_timestamp():
    with pytest.raises(ValueError):
        CashFutureObservation(1.5, 100.0, 105.0)


def test_cash_future_builder_does_not_truncate_fractional_timestamp():
    with pytest.raises(ValueError):
        build_cash_future_observations([{"timestamp_ns": 1.5, "cash_price": 100, "future_price": 105}])


def test_cash_future_rejects_boolean_lot_size():
    with pytest.raises(ValueError):
        CashFutureObservation(1, 100.0, 105.0, lot_size=True)


def test_cash_future_marks_open_position_at_end():
    rows = [CashFutureObservation(1, 100, 110), CashFutureObservation(2, 101, 109)]
    result = backtest_cash_future_basis(rows, entry_basis=5, exit_basis=0, initial_capital=1000)
    assert result.trades == ()
    assert result.final_capital == 1002
    assert result.net_pnl == 2


def test_cash_future_open_position_contributes_to_drawdown():
    rows = [CashFutureObservation(1, 100, 110), CashFutureObservation(2, 95, 115)]
    result = backtest_cash_future_basis(rows, entry_basis=5, exit_basis=0, initial_capital=1000)
    assert result.final_capital == 990
    assert result.max_drawdown == pytest.approx(0.01)


def test_calendar_rejects_negative_timestamp():
    with pytest.raises(ValueError):
        CalendarQuote(-1, "ABC", 20261231, 10, 11)


def test_calendar_rejects_nonpositive_expiry():
    with pytest.raises(ValueError):
        CalendarQuote(1, "ABC", 0, 10, 11)


def test_calendar_rejects_invalid_instrument_class():
    with pytest.raises(ValueError):
        CalendarQuote(1, "ABC", 20261231, 10, 11, instrument_class="BAD")


def test_calendar_adapter_does_not_bypass_liquidity_requirements():
    adapter = CalendarSpreadStrategyAdapter()
    event = {
        "near": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 10, "ask": 11, "strike": 100, "option_type": "CALL"},
        "far": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20261029, "bid": 15, "ask": 16, "strike": 100, "option_type": "CALL"},
        "liquidity": LiquidityPolicy(min_option_volume=100),
    }
    with pytest.raises(ValueError):
        tuple(adapter.entry(event))
