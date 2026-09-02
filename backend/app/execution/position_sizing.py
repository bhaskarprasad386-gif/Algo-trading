"""Deterministic ATR + India VIX dynamic position sizing foundation."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PositionSizingConfig:
    risk_amount: float
    reference_vix: float = 15.0
    min_vix_factor: float = 0.5
    max_vix_factor: float = 1.5
    atr_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.risk_amount <= 0:
            raise ValueError("risk_amount must be positive")
        if self.reference_vix <= 0:
            raise ValueError("reference_vix must be positive")
        if self.min_vix_factor <= 0 or self.max_vix_factor < self.min_vix_factor:
            raise ValueError("invalid VIX factor bounds")
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive")


def calculate_dynamic_quantity(
    entry_price: float,
    stop_loss_price: float,
    atr: float,
    india_vix: float,
    lot_size: int = 1,
    config: PositionSizingConfig | None = None,
) -> int:
    """Return a lot-size-aligned quantity using stop/ATR risk and India VIX scaling.

    Higher ATR or higher India VIX reduces the allowed quantity. The VIX factor is
    bounded so an unusually low/high VIX cannot create an unbounded position.
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("prices must be positive")
    if atr <= 0 or india_vix <= 0:
        raise ValueError("ATR and India VIX must be positive")
    if lot_size < 1:
        raise ValueError("lot_size must be at least 1")

    active_config = config or PositionSizingConfig(risk_amount=10_000.0)
    stop_distance = abs(entry_price - stop_loss_price)
    risk_per_unit = max(stop_distance, atr * active_config.atr_multiplier)
    if risk_per_unit <= 0:
        return 0

    raw_vix_factor = active_config.reference_vix / india_vix
    vix_factor = min(
        max(raw_vix_factor, active_config.min_vix_factor),
        active_config.max_vix_factor,
    )
    risk_budget = active_config.risk_amount * vix_factor
    units = math.floor(risk_budget / risk_per_unit)
    return (units // lot_size) * lot_size
