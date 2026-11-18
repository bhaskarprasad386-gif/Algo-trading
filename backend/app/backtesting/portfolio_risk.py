"""Non-mutating portfolio risk evaluation for mark-to-market replay."""

from dataclasses import dataclass
from math import isfinite

from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskViolation


@dataclass(frozen=True)
class MarkToMarketRisk:
    """Point-in-time risk result derived from portfolio marks."""

    snapshot: PortfolioSnapshot
    total_pnl: float
    maintenance_breach: bool
    drawdown_breach: bool


def evaluate_mark_to_market(portfolio: Portfolio, marks: dict[str, float]) -> MarkToMarketRisk:
    """Evaluate equity/P&L and maintenance/drawdown limits without changing positions."""
    for instrument, mark in marks.items():
        if not isfinite(mark) or mark <= 0:
            raise ValueError(f"mark must be finite and positive for {instrument}")

    snapshot = portfolio.snapshot(marks)
    total_pnl = snapshot.equity - portfolio.initial_cash
    maintenance_breach = bool(snapshot.positions) and snapshot.equity < snapshot.maintenance_margin - 1e-9
    drawdown_breach = (
        portfolio.risk_config.max_drawdown is not None
        and snapshot.drawdown > portfolio.risk_config.max_drawdown + 1e-9
    )
    if drawdown_breach:
        raise RiskViolation("max drawdown exceeded at mark-to-market")
    return MarkToMarketRisk(snapshot, total_pnl, maintenance_breach, drawdown_breach)
