import pytest

from app.backtesting.config import DEFAULT_PAPER_CAPITAL
from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation


def fill(order_id, instrument, side, quantity, price, fee=0.0):
    return SimFill(order_id, instrument, side, quantity, price, 1, fee)


def test_default_capital_is_one_crore_and_equity_includes_marked_position_value():
    p = Portfolio()
    assert p.initial_cash == DEFAULT_PAPER_CAPITAL == 10_000_000.0
    p.apply_fill(fill("b1", "NIFTY", ExecutionSide.BUY, 10, 100))
    s = p.snapshot({"NIFTY": 110})
    assert s.cash == 9_999_000
    assert s.unrealized_pnl == 100
    assert s.equity == 10_000_100


def test_realized_pnl_is_cumulative_after_position_is_closed():
    p = Portfolio(100_000)
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    p.apply_fill(fill("s", "X", ExecutionSide.SELL, 10, 110))
    s = p.snapshot()
    assert s.positions == ()
    assert s.realized_pnl == 100
    assert s.equity == 100_100


def test_fees_are_accounted_separately_and_reduce_equity():
    p = Portfolio(100_000)
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100, 25))
    p.apply_fill(fill("s", "X", ExecutionSide.SELL, 10, 110, 25))
    s = p.snapshot()
    assert s.fees == 50
    assert s.equity == 100_050
    assert s.realized_pnl == 100


def test_margin_reserve_and_release_tracks_gross_exposure():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2, maintenance_margin_rate=0.1))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    s = p.snapshot({"X": 100})
    assert s.gross_notional == 10_000
    assert s.initial_margin == 2_000
    assert s.available_margin == 98_000
    p.apply_fill(fill("s", "X", ExecutionSide.SELL, 100, 100))
    assert p.snapshot().initial_margin == 0


def test_insufficient_margin_rejects_projected_position():
    p = Portfolio(1_000, RiskConfig(initial_margin_rate=1.0))
    with pytest.raises(RiskViolation, match="insufficient available margin"):
        p.apply_fill(fill("b", "X", ExecutionSide.BUY, 11, 100))


def test_max_position_and_notional_limits_are_enforced():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_position_quantity=10, max_gross_notional=1_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    with pytest.raises(RiskViolation, match="max position quantity"):
        p.apply_fill(fill("b2", "X", ExecutionSide.BUY, 1, 100))


def test_leverage_limit_is_enforced():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.1, max_leverage=2.0))
    with pytest.raises(RiskViolation, match="max leverage"):
        p.apply_fill(fill("b", "X", ExecutionSide.BUY, 201, 100))
