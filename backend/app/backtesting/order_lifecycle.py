"""Deterministic order lifecycle for backtest and paper execution."""

from dataclasses import dataclass
from enum import Enum

from app.backtesting.execution import ExecutionSide, OrderType, SimFill, SimOrder


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class OrderState:
    order: SimOrder
    status: OrderStatus
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    reject_reason: str | None = None


class OrderLifecycle:
    """State machine enforcing legal order transitions and fill accounting."""

    TERMINAL = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}

    def __init__(self, order: SimOrder) -> None:
        self.state = OrderState(order=order, status=OrderStatus.SUBMITTED)

    def accept(self) -> OrderState:
        self._require(OrderStatus.SUBMITTED)
        self.state = OrderState(self.state.order, OrderStatus.ACCEPTED, self.state.filled_quantity, self.state.average_fill_price)
        return self.state

    def reject(self, reason: str) -> OrderState:
        if self.state.status in self.TERMINAL:
            raise ValueError("terminal order cannot be rejected")
        if not reason.strip():
            raise ValueError("reject reason is required")
        self.state = OrderState(self.state.order, OrderStatus.REJECTED, self.state.filled_quantity, self.state.average_fill_price, reason)
        return self.state

    def cancel(self) -> OrderState:
        if self.state.status in self.TERMINAL:
            raise ValueError("terminal order cannot be cancelled")
        self.state = OrderState(self.state.order, OrderStatus.CANCELLED, self.state.filled_quantity, self.state.average_fill_price)
        return self.state

    def apply_fill(self, fill: SimFill) -> OrderState:
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("order must be accepted before filling")
        if fill.order_id != self.state.order.order_id:
            raise ValueError("fill order_id does not match order")
        if fill.instrument != self.state.order.instrument or fill.side != self.state.order.side:
            raise ValueError("fill does not match order")
        remaining = self.state.order.quantity - self.state.filled_quantity
        if fill.quantity <= 0 or fill.quantity > remaining:
            raise ValueError("fill quantity exceeds remaining order quantity")
        total_qty = self.state.filled_quantity + fill.quantity
        average = ((self.state.filled_quantity * self.state.average_fill_price) + (fill.quantity * fill.price)) / total_qty
        status = OrderStatus.FILLED if total_qty == self.state.order.quantity else OrderStatus.PARTIALLY_FILLED
        self.state = OrderState(self.state.order, status, total_qty, average)
        return self.state

    def to_fill(self, price: float, timestamp_ns: int, quantity: int | None = None, fee: float = 0.0) -> SimFill:
        remaining = self.state.order.quantity - self.state.filled_quantity
        qty = remaining if quantity is None else quantity
        if qty <= 0 or qty > remaining:
            raise ValueError("invalid fill quantity")
        return SimFill(self.state.order.order_id, self.state.order.instrument, self.state.order.side, qty, price, timestamp_ns, fee)

    def _require(self, expected: OrderStatus) -> None:
        if self.state.status != expected:
            raise ValueError(f"expected {expected.value}, got {self.state.status.value}")
