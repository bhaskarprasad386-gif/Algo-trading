"""Mark-to-market portfolio risk monitoring and maintenance-margin state."""

from dataclasses import dataclass

from app.backtesting.portfolio import PortfolioSnapshot, RiskViolation


@dataclass(frozen=True)
class MarginRiskState:
    """Point-in-time maintenance-margin state for a portfolio snapshot."""

    equity: float
    initial_margin: float
    maintenance_margin: float
    reserved_margin: float
    available_margin: float
    margin_call: bool
    liquidation_required: bool

    @property
    def margin_buffer(self) -> float:
        """Equity remaining above maintenance margin."""
        return self.equity - self.maintenance_margin


def evaluate_margin(snapshot: PortfolioSnapshot) -> MarginRiskState:
    """Evaluate maintenance margin without mutating portfolio state."""
    margin_call = snapshot.equity + 1e-9 < snapshot.maintenance_margin
    liquidation_required = margin_call and bool(snapshot.positions)
    return MarginRiskState(
        equity=snapshot.equity,
        initial_margin=snapshot.initial_margin,
        maintenance_margin=snapshot.maintenance_margin,
        reserved_margin=snapshot.reserved_margin,
        available_margin=snapshot.available_margin,
        margin_call=margin_call,
        liquidation_required=liquidation_required,
    )


def enforce_new_risk(snapshot: PortfolioSnapshot) -> None:
    """Block new risk while a marked portfolio is below maintenance margin."""
    state = evaluate_margin(snapshot)
    if state.margin_call:
        raise RiskViolation("maintenance margin call: new risk is blocked")
