"""Mark-to-market portfolio risk checks used by the event-driven backtest engine."""

from dataclasses import dataclass
from typing import Mapping

from app.backtesting.portfolio import Portfolio, RiskViolation


@dataclass(frozen=True)
class MarketRiskState:
    """Point-in-time risk state derived from portfolio marks."""

    equity: float
    gross_notional: float
    net_notional: float
    initial_margin: float
    maintenance_margin: float
    available_margin: float
    leverage: float
    drawdown: float
    margin_call: bool
    max_drawdown_breached: bool


def evaluate_market_risk(portfolio: Portfolio, marks: Mapping[str, float] | None = None) -> MarketRiskState:
    """Evaluate mark-to-market risk without mutating portfolio state."""
    snapshot = portfolio.snapshot(dict(marks or {}))
    return MarketRiskState(
        equity=snapshot.equity,
        gross_notional=snapshot.gross_notional,
        net_notional=snapshot.net_notional,
        initial_margin=snapshot.initial_margin,
        maintenance_margin=snapshot.maintenance_margin,
        available_margin=snapshot.available_margin,
        leverage=snapshot.leverage,
        drawdown=snapshot.drawdown,
        margin_call=snapshot.equity + 1e-9 < snapshot.maintenance_margin,
        max_drawdown_breached=(
            portfolio.risk_config.max_drawdown is not None
            and snapshot.drawdown > portfolio.risk_config.max_drawdown + 1e-9
        ),
    )


def enforce_market_risk(portfolio: Portfolio, marks: Mapping[str, float] | None = None) -> MarketRiskState:
    """Fail closed when current marks breach any configured hard risk limit."""
    state = evaluate_market_risk(portfolio, marks)
    if state.margin_call:
        raise RiskViolation("maintenance margin breached")
    if state.max_drawdown_breached:
        raise RiskViolation("max drawdown exceeded")
    if portfolio.risk_config.max_gross_notional is not None and state.gross_notional > portfolio.risk_config.max_gross_notional + 1e-9:
        raise RiskViolation("max gross notional exceeded")
    if portfolio.risk_config.max_net_notional is not None and abs(state.net_notional) > portfolio.risk_config.max_net_notional + 1e-9:
        raise RiskViolation("max net notional exceeded")
    if portfolio.risk_config.max_leverage is not None and state.equity > 0 and state.leverage > portfolio.risk_config.max_leverage + 1e-9:
        raise RiskViolation("max leverage exceeded")
    return state
