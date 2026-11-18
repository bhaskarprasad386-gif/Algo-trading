import pytest

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation


def _fill(order_id: str, instrument: str, side: ExecutionSide, price: float = 10.0) -> SimFill:
    return SimFill(
        order_id=order_id,
        instrument=instrument,
        side=side,
        quantity=100,
        price=price,
        filled_at_ns=1,
    )


def test_atomic_multi_leg_risk_uses_final_net_exposure() -> None:
    portfolio = Portfolio(
        initial_cash=1_000.0,
        risk_config=RiskConfig(
            initial_margin_rate=0.1,
            max_net_notional=100.0,
        ),
    )

    with pytest.raises(RiskViolation, match="max net notional"):
        portfolio.apply_fill(_fill("single-buy", "LEG_A", ExecutionSide.BUY))

    snapshot = portfolio.apply_fills_atomic(
        (
            _fill("buy-a", "LEG_A", ExecutionSide.BUY),
            _fill("sell-b", "LEG_B", ExecutionSide.SELL),
        ),
        marks={"LEG_A": 10.0, "LEG_B": 10.0},
    )

    assert snapshot.net_notional == 0.0
    assert snapshot.gross_notional == 2_000.0
    assert snapshot.cash == 1_000.0
    assert snapshot.equity == 1_000.0
    assert len(portfolio.trades) == 2
