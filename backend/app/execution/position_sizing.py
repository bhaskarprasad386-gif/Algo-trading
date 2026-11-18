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
        for value, name in (
            (self.risk_amount, "risk_amount"),
            (self.reference_vix, "reference_vix"),
            (self.min_vix_factor, "min_vix_factor"),
            (self.max_vix_factor, "max_vix_factor"),
            (self.atr_multiplier, "atr_multiplier"),
        ):
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
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
    for value, name in (
        (entry_price, "entry_price"),
        (stop_loss_price, "stop_loss_price"),
        (atr, "atr"),
        (india_vix, "india_vix"),
    ):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size < 1:
        raise ValueError("lot_size must be a positive integer")
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("prices must be positive")
    if atr <= 0 or india_vix <= 0:
        raise ValueError("ATR and India VIX must be positive")

    active_config = config or PositionSizingConfig(risk_amount=10_000.0)
    stop_distance = abs(entry_price - stop_loss_price)
    risk_per_unit = max(stop_distance, atr * active_config.atr_multiplier)
    if risk_per_unit <= 0 or not math.isfinite(risk_per_unit):
        raise ValueError("risk_per_unit must be finite and positive")

    raw_vix_factor = active_config.reference_vix / india_vix
    vix_factor = min(
        max(raw_vix_factor, active_config.min_vix_factor),
        active_config.max_vix_factor,
    )
    risk_budget = active_config.risk_amount * vix_factor
    if not math.isfinite(risk_budget) or risk_budget <= 0:
        raise ValueError("risk_budget must be finite and positive")
    units = math.floor(risk_budget / risk_per_unit)
    return (units // lot_size) * lot_size
