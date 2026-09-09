from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, RiskConfig


def test_buy_mark_sell_tracks_realized_and_unrealized_pnl_separately():
    portfolio = Portfolio(initial_cash=1_000_000)

    portfolio.apply_fill(
        SimFill("buy-1", "NSE:SBIN", ExecutionSide.BUY, 10, 100.0, 1_000)
    )
    marked = portfolio.snapshot({"NSE:SBIN": 110.0})

    assert marked.cash == 999_000.0
    assert marked.realized_pnl == 0.0
    assert marked.unrealized_pnl == 100.0
    assert marked.equity == 1_000_100.0

    portfolio.apply_fill(
        SimFill("sell-1", "NSE:SBIN", ExecutionSide.SELL, 10, 120.0, 2_000)
    )
    closed = portfolio.snapshot({"NSE:SBIN": 120.0})

    assert closed.cash == 1_000_200.0
    assert closed.realized_pnl == 200.0
    assert closed.unrealized_pnl == 0.0
    assert closed.equity == 1_000_200.0
    assert closed.positions == ()


def test_short_position_pnl_is_signed_correctly():
    portfolio = Portfolio(initial_cash=1_000_000)

    portfolio.apply_fill(
        SimFill("short-1", "NSE:SBIN", ExecutionSide.SELL, 10, 120.0, 1_000)
    )
    marked = portfolio.snapshot({"NSE:SBIN": 110.0})

    assert marked.cash == 1_001_200.0
    assert marked.unrealized_pnl == 100.0
    assert marked.equity == 1_000_100.0

    portfolio.apply_fill(
        SimFill("cover-1", "NSE:SBIN", ExecutionSide.BUY, 10, 100.0, 2_000)
    )
    closed = portfolio.snapshot({"NSE:SBIN": 100.0})

    assert closed.realized_pnl == 200.0
    assert closed.unrealized_pnl == 0.0
    assert closed.equity == 1_000_200.0


def test_atomic_fill_failure_restores_pnl_cash_positions_and_trade_history():
    portfolio = Portfolio(
        initial_cash=1_000_000,
        risk_config=RiskConfig(max_position_quantity=10),
    )
    portfolio.apply_fill(
        SimFill("seed", "NSE:SBIN", ExecutionSide.BUY, 10, 100.0, 1_000)
    )
    before = portfolio.snapshot({"NSE:SBIN": 110.0})
    before_trades = portfolio.trades

    try:
        portfolio.apply_fills_atomic(
            (
                SimFill("close", "NSE:SBIN", ExecutionSide.SELL, 10, 120.0, 2_000),
                SimFill("bad", "NSE:RELIANCE", ExecutionSide.BUY, 11, 200.0, 2_001),
            ),
            {"NSE:SBIN": 120.0, "NSE:RELIANCE": 200.0},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected atomic portfolio application to reject the second leg")

    after = portfolio.snapshot({"NSE:SBIN": 110.0})
    assert after == before
    assert portfolio.trades == before_trades
