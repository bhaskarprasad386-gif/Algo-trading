from dataclasses import dataclass
from datetime import datetime, timezone
import math
from threading import Lock


@dataclass(frozen=True)
class RiskLimits:
    max_orders_per_day: int = 20
    max_quantity_per_order: int = 1000
    max_position_quantity: int = 5000
    max_loss: float = 10000.0

    def __post_init__(self) -> None:
        for value, name in (
            (self.max_orders_per_day, "max_orders_per_day"),
            (self.max_quantity_per_order, "max_quantity_per_order"),
            (self.max_position_quantity, "max_position_quantity"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(float(self.max_loss)) or self.max_loss <= 0:
            raise ValueError("max_loss must be finite and positive")


class RiskEngine:
    """Fail-closed paper pre-trade checks with atomic reservation and P&L tracking."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()
        self._orders_today = 0
        self._day = datetime.now(timezone.utc).date()
        self._positions: dict[str, int] = {}
        self._average_prices: dict[str, float] = {}
        self._realized_pnl = 0.0
        self._lock = Lock()

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self._orders_today = 0
            self._realized_pnl = 0.0

    def check(self, quantity: int, current_position: int = 0, realized_pnl: float = 0.0) -> tuple[bool, str]:
        with self._lock:
            return self._check_unlocked(quantity, current_position, realized_pnl)

    def _check_unlocked(self, quantity: int, current_position: int, realized_pnl: float, projected_position: int | None = None) -> tuple[bool, str]:
        self._roll_day()
        for value, name in ((quantity, "quantity"), (current_position, "current_position"), (realized_pnl, "realized_pnl")):
            if isinstance(value, bool) or not math.isfinite(float(value)):
                return False, f"{name} must be finite"
        if projected_position is not None and (isinstance(projected_position, bool) or not isinstance(projected_position, int)):
            return False, "projected_position must be an integer"
        if isinstance(quantity, bool) or isinstance(current_position, bool) or int(quantity) != quantity or int(current_position) != current_position:
            return False, "quantity and current_position must be integers"
        if quantity <= 0:
            return False, "quantity must be greater than zero"
        if quantity > self.limits.max_quantity_per_order:
            return False, "quantity exceeds per-order risk limit"
        if self._orders_today >= self.limits.max_orders_per_day:
            return False, "daily order limit reached"
        position_after = projected_position if projected_position is not None else current_position + quantity
        if abs(position_after) > self.limits.max_position_quantity:
            return False, "position limit exceeded"
        if realized_pnl <= -self.limits.max_loss:
            return False, "maximum loss limit reached"
        return True, "risk checks passed"

    def check_and_reserve(self, symbol: str, transaction_type: str, quantity: int, price: float) -> None:
        """Atomically check, update position/P&L state and reserve a paper order."""
        symbol = symbol.strip().upper()
        transaction_type = transaction_type.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        if transaction_type not in {"BUY", "SELL"}:
            raise ValueError("transaction_type must be BUY or SELL")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not math.isfinite(float(price)) or price <= 0:
            raise ValueError("price must be finite and positive for paper risk accounting")
        with self._lock:
            current = self._positions.get(symbol, 0)
            average = self._average_prices.get(symbol, price)
            signed = quantity if transaction_type == "BUY" else -quantity
            projected = current + signed
            allowed, reason = self._check_unlocked(quantity, current, self._realized_pnl, projected_position=projected)
            if not allowed:
                raise ValueError(reason)

            realized_delta = 0.0
            if current == 0 or (current > 0 and signed > 0) or (current < 0 and signed < 0):
                total = abs(current) + abs(signed)
                average = ((abs(current) * average) + (abs(signed) * price)) / total
            else:
                closed = min(abs(current), abs(signed))
                direction = 1 if current > 0 else -1
                realized_delta = closed * (price - average) * direction
                projected_realized = self._realized_pnl + realized_delta
                if projected_realized <= -self.limits.max_loss:
                    raise ValueError("maximum loss limit reached")
                self._realized_pnl = projected_realized
                if projected == 0:
                    self._average_prices.pop(symbol, None)
                elif current * projected < 0:
                    average = price

            self._orders_today += 1
            self._positions[symbol] = projected
            if projected != 0:
                self._average_prices[symbol] = average

    def reserve_order(self) -> None:
        with self._lock:
            self._roll_day()
            if self._orders_today >= self.limits.max_orders_per_day:
                raise ValueError("daily order limit reached")
            self._orders_today += 1

    @property
    def orders_today(self) -> int:
        with self._lock:
            self._roll_day()
            return self._orders_today
