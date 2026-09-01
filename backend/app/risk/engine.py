from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RiskLimits:
    max_orders_per_day: int = 20
    max_quantity_per_order: int = 1000
    max_position_quantity: int = 5000
    max_loss: float = 10000.0


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
        if quantity <= 0:
            return False, "quantity must be greater than zero"
        if quantity > self.limits.max_quantity_per_order:
            return False, "quantity exceeds per-order risk limit"
        if self._orders_today >= self.limits.max_orders_per_day:
            return False, "daily order limit reached"
        if abs(current_position) + quantity > self.limits.max_position_quantity:
            return False, "position limit exceeded"
        if realized_pnl <= -abs(self.limits.max_loss):
            return False, "maximum loss limit reached"
        return True, "risk checks passed"

    def reserve_order(self) -> None:
        self._roll_day()
        self._orders_today += 1

    @property
    def orders_today(self) -> int:
        self._roll_day()
        return self._orders_today
