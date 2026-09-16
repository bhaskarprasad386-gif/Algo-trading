import pytest

from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, Position, RiskConfig, RiskViolation


def test_depth_rejects_fractional_quantity_at_event_boundary():
    with pytest.raises(TypeError, match="bids quantity must be an integer"):
        MarketEvent(1, "X", EventType.DEPTH, {"bids": [(100, 1.5)]})


def test_depth_rejects_string_quantity_at_event_boundary():
    with pytest.raises(TypeError, match="asks quantity must be an integer"):
        MarketEvent(1, "X", EventType.DEPTH, {"asks": [(101, "10")]})


def test_forced_liquidation_bypasses_entry_margin_and_drawdown_gates():
    p = Portfolio(
        1_000,
        RiskConfig(
            initial_margin_rate=1.0,
            maintenance_margin_rate=0.9,
            max_drawdown=10,
        ),
    )
    p.cash = -100.0
    p._positions["X"] = Position("X", 10, 100.0)

    with pytest.raises(RiskViolation, match="maintenance margin breached"):
        p.validate_mark_to_market({"X": 20.0})

    fill = SimFill("liq", "X", ExecutionSide.SELL, 5, 20.0, 2)
    snapshot = p.forced_liquidation([fill], {"X": 20.0})

    assert snapshot.equity == pytest.approx(100.0)
    assert snapshot.maintenance_margin == pytest.approx(90.0)
    assert snapshot.positions[0].quantity == 5
    assert len(p.trades) == 1
