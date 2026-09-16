import pytest

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, Position


def test_position_rejects_invalid_constructor_values() -> None:
    with pytest.raises(ValueError, match="instrument"):
        Position("", 1, 10.0)
    with pytest.raises(ValueError, match="quantity"):
        Position("NSE:TEST", True, 10.0)
    with pytest.raises(ValueError, match="average_price"):
        Position("NSE:TEST", 1, 0.0)
    with pytest.raises(ValueError, match="realized_pnl"):
        Position("NSE:TEST", 1, 10.0, float("nan"))


def test_snapshot_rejects_missing_mark_instead_of_using_average_price() -> None:
    portfolio = Portfolio(initial_cash=1_000.0)
    portfolio.apply_fill(SimFill("entry", "NSE:TEST", ExecutionSide.BUY, 10, 10.0, 1))

    with pytest.raises(ValueError, match="missing market mark"):
        portfolio.snapshot()

    snapshot = portfolio.snapshot({"NSE:TEST": 8.0})
    assert snapshot.equity == pytest.approx(980.0)
    assert snapshot.unrealized_pnl == pytest.approx(-20.0)


def test_apply_fill_can_mark_new_position_with_fill_price() -> None:
    portfolio = Portfolio(initial_cash=1_000.0)
    position = portfolio.apply_fill(
        SimFill("entry", "NSE:TEST", ExecutionSide.BUY, 10, 10.0, 1)
    )

    assert position.quantity == 10
    assert portfolio.snapshot({"NSE:TEST": 10.0}).equity == pytest.approx(1_000.0)
