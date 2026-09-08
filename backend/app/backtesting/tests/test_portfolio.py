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


def test_explicit_margin_reservation_reduces_available_capital_and_releases():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2, maintenance_margin_rate=0.1))
    p.reserve_margin("pending", 15_000)
    assert p.snapshot().reserved_margin == 15_000
    assert p.snapshot().available_margin == 85_000
    p.release_margin("pending")
    assert p.snapshot().reserved_margin == 0
    assert p.snapshot().available_margin == 100_000


def test_insufficient_margin_rejects_projected_position():
    p = Portfolio(1_000, RiskConfig(initial_margin_rate=1.0))
    with pytest.raises(RiskViolation, match="insufficient available margin"):
        p.apply_fill(fill("b", "X", ExecutionSide.BUY, 11, 100))


def test_fill_respects_other_pending_margin_reservations():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5))
    p.reserve_margin("other-order", 60_000)
    with pytest.raises(RiskViolation, match="insufficient available margin"):
        p.apply_fill(fill("fill-order", "X", ExecutionSide.BUY, 100, 1_000))


def test_max_position_and_notional_limits_are_enforced():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_position_quantity=10, max_gross_notional=1_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    with pytest.raises(RiskViolation, match="max position quantity"):
        p.apply_fill(fill("b2", "X", ExecutionSide.BUY, 1, 100))


def test_leverage_limit_is_enforced():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.1, max_leverage=2.0))
    with pytest.raises(RiskViolation, match="max leverage"):
        p.apply_fill(fill("b", "X", ExecutionSide.BUY, 201, 100))


def test_atomic_multi_leg_failure_rolls_back_all_portfolio_changes():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_position_quantity=5))
    fills = (fill("leg1", "A", ExecutionSide.BUY, 5, 100), fill("leg2", "B", ExecutionSide.BUY, 6, 100))
    with pytest.raises(RiskViolation, match="max position quantity"):
        p.apply_fills_atomic(fills)
    s = p.snapshot()
    assert s.positions == ()
    assert s.cash == 100_000
    assert p.trades == ()


def test_max_drawdown_rejects_fill_before_mutating_state():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, max_drawdown=100))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    before = p.export_state()
    with pytest.raises(RiskViolation, match="max drawdown"):
        p.apply_fill(fill("s", "X", ExecutionSide.SELL, 10, 80))
    assert p.export_state() == before


def test_trade_ledger_records_capital_and_realized_pnl():
    p = Portfolio(100_000)
    p.apply_fill(fill("buy", "X", ExecutionSide.BUY, 10, 100, 2))
    p.apply_fill(fill("sell", "X", ExecutionSide.SELL, 10, 110, 3))
    assert len(p.trades) == 2
    assert p.trades[0].gross_value == 1_000
    assert p.trades[0].cash_after == 98_998
    assert p.trades[1].realized_pnl_delta == 100
    assert p.trades[1].fee == 3
