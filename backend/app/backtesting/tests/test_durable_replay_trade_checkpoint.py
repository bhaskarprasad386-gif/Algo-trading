import pytest

from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.portfolio import Portfolio


def test_restore_trade_state_rejects_fractional_quantity_without_coercion():
    portfolio = Portfolio(100_000)
    raw_state = [
        {
            "order_id": "o1",
            "instrument": "X",
            "side": "BUY",
            "quantity": 1.5,
            "price": 100.0,
            "gross_value": 150.0,
            "fee": 0.0,
            "realized_pnl_delta": 0.0,
            "cash_after": 99_850.0,
            "equity_after": 100_000.0,
            "timestamp_ns": 1,
        }
    ]
    with pytest.raises(ValueError, match="invalid portfolio trade checkpoint"):
        DurableEventBacktestEngine._restore_trade_state(portfolio, raw_state)
    assert portfolio.trades == ()


def test_restore_trade_state_accepts_positive_integer_quantity():
    portfolio = Portfolio(100_000)
    raw_state = [
        {
            "order_id": "o1",
            "instrument": "X",
            "side": "BUY",
            "quantity": 2,
            "price": 100.0,
            "gross_value": 200.0,
            "fee": 0.0,
            "realized_pnl_delta": 0.0,
            "cash_after": 99_800.0,
            "equity_after": 100_000.0,
            "timestamp_ns": 1,
        }
    ]
    DurableEventBacktestEngine._restore_trade_state(portfolio, raw_state)
    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].quantity == 2
