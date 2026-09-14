from dataclasses import dataclass
from datetime import datetime, timezone
import math


@dataclass(frozen=True)
class RiskLimits:
    max_orders_per_day: int = 20
    max_quantity_per_order: int = 1000
    max_position_quantity: int = 5000
    max_loss: float = 10000.0

    def __post_init__(self) -> None:
        if self.max_orders_per_day <= 0:
            raise ValueError("max_orders_per_day must be positive")
        if self.max_quantity_per_order <= 0:
            raise ValueError("max_quantity_per_order must be positive")
        if self.max_position_quantity <= 0:
            raise ValueError("max_position_quantity must be positive")
        if not math.isfinite(float(self.max_loss)) or self.max_loss <= 0:
            raise ValueError("max_loss must be finite and positive")


class RiskEngine:
    """Fail-closed pre-trade checks. Broker execution is never performed here."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()
        self._orders_today = 0
        self._day = datetime.now(timezone.utc).date()

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self._orders_today = 0

    def check(self, quantity: int, current_position: int = 0, realized_pnl: float = 0.0) -> tuple[bool, str]:
        self._roll_day()
        for value, name in ((quantity, "quantity"), (current_position, "current_position"), (realized_pnl, "realized_pnl")):
            if not math.isfinite(float(value)):
                return False, f"{name} must be finite"
        if int(quantity) != quantity or int(current_position) != current_position:
            return False, "quantity and current_position must be integers"
        if quantity <= 0:
            return False, "quantity must be greater than zero"
        if quantity > self.limits.max_quantity_per_order:
            return False, "quantity exceeds per-order risk limit"
        if self._orders_today >= self.limits.max_orders_per_day:
            return False, "daily order limit reached"
        if abs(current_position) + quantity > self.limits.max_position_quantity:
            return False, "position limit exceeded"
        if realized_pnl <= -self.limits.max_loss:
            return False, "maximum loss limit reached"
        return True, "risk checks passed"

    def reserve_order(self) -> None:
        self._roll_day()
        if self._orders_today >= self.limits.max_orders_per_day:
            raise ValueError("daily order limit reached")
        self._orders_today += 1

    @property
    def orders_today(self) -> int:
        self._roll_day()
        return self._orders_today
