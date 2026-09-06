"""Deterministic capital and margin guard for backtest/paper execution."""

from dataclasses import dataclass

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio


@dataclass(frozen=True)
class MarginConfig:
    initial_margin_rate: float = 1.0
    maintenance_margin_rate: float = 1.0

    def __post_init__(self) -> None:
        if not 0 < self.initial_margin_rate <= 1:
            raise ValueError("initial_margin_rate must be in (0, 1]")
        if not 0 < self.maintenance_margin_rate <= 1:
            raise ValueError("maintenance_margin_rate must be in (0, 1]")


@dataclass(frozen=True)
class RiskCheck:
    approved: bool
    required_margin: float
    available_cash: float
    reason: str


class RiskManager:
    """Conservative notional-margin gate shared by backtest and paper flows."""

    def __init__(self, config: MarginConfig | None = None) -> None:
        self.config = config or MarginConfig()

    def check_fill(self, portfolio: Portfolio, fill: SimFill) -> RiskCheck:
        notional = fill.quantity * fill.price
        required = notional * self.config.initial_margin_rate
        available = portfolio.cash
        if fill.side == ExecutionSide.BUY:
            approved = available >= required + fill.fee
        else:
            # Short-selling margin is reserved from cash until instrument-specific
            # derivatives margin rules are supplied by the contract metadata.
            approved = available >= required + fill.fee
        return RiskCheck(
            approved=approved,
            required_margin=required,
            available_cash=available,
            reason="approved" if approved else "insufficient margin",
        )

    def apply_if_approved(self, portfolio: Portfolio, fill: SimFill):
        check = self.check_fill(portfolio, fill)
        if not check.approved:
            raise ValueError(check.reason)
        return portfolio.apply_fill(fill)
