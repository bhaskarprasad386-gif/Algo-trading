"""Mark-to-market portfolio risk checks used by the event-driven backtest engine."""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.backtesting.execution import ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio, RiskViolation


class MarginCallState(str, Enum):
    """Deterministic lifecycle for maintenance-margin status."""

    NORMAL = "normal"
    MARGIN_CALL = "margin_call"
    RECOVERED = "recovered"


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


def transition_margin_call_state(
    previous: MarginCallState,
    state: MarketRiskState,
) -> MarginCallState:
    """Advance margin-call state from a previous state and current MTM risk.

    A breach enters ``MARGIN_CALL``. Once a previously breached portfolio is back
    at or above maintenance margin it transitions to ``RECOVERED``. A healthy
    portfolio starts in ``NORMAL``. ``RECOVERED`` remains a terminal acknowledgement
    for that observation; the next healthy evaluation can explicitly start NORMAL.
    """
    if state.margin_call:
        return MarginCallState.MARGIN_CALL
    if previous == MarginCallState.MARGIN_CALL:
        return MarginCallState.RECOVERED
    return previous if previous == MarginCallState.RECOVERED else MarginCallState.NORMAL


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


def order_reduces_position_risk(portfolio: Portfolio, order: SimOrder) -> bool:
    """Return whether an order strictly reduces the signed position exposure.

    A risk-reducing close is allowed to proceed during a margin call so a caller can
    unwind exposure instead of being trapped by a fail-closed new-risk gate.
    Orders that open, add to, or reverse a position are not considered reducing.
    """
    position = next((p for p in portfolio.snapshot().positions if p.instrument == order.instrument), None)
    current = position.quantity if position is not None else 0
    if current == 0:
        return False
    signed = order.quantity if order.side == ExecutionSide.BUY else -order.quantity
    projected = current + signed
    return abs(projected) < abs(current) and (current * projected >= 0 or projected == 0)
