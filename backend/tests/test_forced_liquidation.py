import pytest

from backend.app.backtesting.execution import ExecutionSide, SimFill
from backend.app.backtesting.forced_liquidation import ForcedLiquidationEngine
from backend.app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation


def _breached_short() -> Portfolio:
    portfolio = Portfolio(
        initial_cash=1_000.0,
        risk_config=RiskConfig(initial_margin_rate=0.50, maintenance_margin_rate=0.40),
    )
    portfolio.apply_fill(SimFill("entry", "NSE:TEST", ExecutionSide.SELL, 100, 10.0, 1))
    return portfolio


def test_no_breach_does_not_liquidate() -> None:
    portfolio = Portfolio(initial_cash=1_000.0)
    portfolio.apply_fill(SimFill("entry", "NSE:TEST", ExecutionSide.BUY, 10, 10.0, 1))
    result = ForcedLiquidationEngine().liquidate(
        portfolio, marks={"NSE:TEST": 10.0}, executable_prices={"NSE:TEST": 10.0}, timestamp_ns=2
    )
    assert not result.triggered
    assert result.fills == ()
    assert portfolio.trades[-1].order_id == "entry"


def test_breach_liquidates_short_and_records_audit_trade() -> None:
    portfolio = _breached_short()
    assert portfolio.snapshot({"NSE:TEST": 19.0}).equity < portfolio.snapshot({"NSE:TEST": 19.0}).maintenance_margin

    result = ForcedLiquidationEngine().liquidate(
        portfolio, marks={"NSE:TEST": 19.0}, executable_prices={"NSE:TEST": 19.0}, timestamp_ns=2
    )

    assert result.triggered
    assert len(result.fills) == 1
    assert result.fills[0].side == ExecutionSide.BUY
    assert result.fills[0].quantity == 100
    assert result.snapshot.positions == ()
    assert result.snapshot.equity >= result.snapshot.maintenance_margin
    assert portfolio.trades[-1].order_id == "forced-liquidation:NSE:TEST"


def test_missing_price_never_fabricates_exit() -> None:
    portfolio = _breached_short()
    result = ForcedLiquidationEngine().liquidate(
        portfolio, marks={"NSE:TEST": 19.0}, executable_prices={}, timestamp_ns=2
    )
    assert result.fills == ()
    assert result.unresolved_instruments == ("NSE:TEST",)
    assert portfolio.snapshot({"NSE:TEST": 19.0}).positions[0].quantity == -100


def test_forced_liquidation_never_increases_or_reverses_exposure() -> None:
    portfolio = _breached_short()
    with pytest.raises(RiskViolation, match="cannot increase or reverse exposure"):
        portfolio.forced_liquidation(
            [SimFill("bad", "NSE:TEST", ExecutionSide.SELL, 101, 19.0, 2)],
            {"NSE:TEST": 19.0},
        )
    assert portfolio.snapshot({"NSE:TEST": 19.0}).positions[0].quantity == -100


def test_multiple_liquidations_are_atomic_on_validation_failure() -> None:
    portfolio = Portfolio(
        initial_cash=1_000.0,
        risk_config=RiskConfig(initial_margin_rate=0.50, maintenance_margin_rate=0.40),
    )
    portfolio.apply_fill(SimFill("e1", "A", ExecutionSide.SELL, 50, 10.0, 1))
    portfolio.apply_fill(SimFill("e2", "B", ExecutionSide.SELL, 50, 10.0, 2))
    before = portfolio.export_state()
    with pytest.raises(RiskViolation):
        portfolio.apply_fills_atomic(
            [
                SimFill("l1", "A", ExecutionSide.BUY, 50, 19.0, 3),
                SimFill("l2", "B", ExecutionSide.SELL, 51, 19.0, 3),
            ],
            {"A": 19.0, "B": 19.0},
        )
    assert portfolio.export_state() == before
