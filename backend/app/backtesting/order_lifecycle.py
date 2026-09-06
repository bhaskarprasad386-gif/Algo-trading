"""Deterministic open-order lifecycle for universal backtests."""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.backtesting.execution import ExecutionSide, OrderType, SimFill, SimOrder, TimeInForce


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REPLACED = "REPLACED"


@dataclass(frozen=True)
class LifecycleEvent:
    order_id: str
    status: OrderStatus
    timestamp_ns: int
    filled_quantity: int
    remaining_quantity: int
    reason: str | None = None
    replacement_order_id: str | None = None


@dataclass(frozen=True)
class OrderState:
    order: SimOrder
    status: OrderStatus
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    reject_reason: str | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    events: tuple[LifecycleEvent, ...] = ()

    @property
    def remaining_quantity(self) -> int:
        return self.order.quantity - self.filled_quantity

    @property
    def terminal(self) -> bool:
        return self.status in {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REPLACED}


class OrderLifecycle:
    """State machine enforcing legal transitions and auditable fill accounting."""

    TERMINAL = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REPLACED}

    def __init__(self, order: SimOrder, time_in_force: TimeInForce | None = None) -> None:
        tif = order.time_in_force if time_in_force is None else time_in_force
        if not isinstance(tif, TimeInForce):
            raise ValueError("invalid time_in_force")
        self.state = OrderState(order=order, status=OrderStatus.SUBMITTED, time_in_force=tif)

    def _transition(self, status: OrderStatus, timestamp_ns: int, *, reason: str | None = None, replacement_order_id: str | None = None) -> OrderState:
        if timestamp_ns < self.state.order.submitted_at_ns:
            raise ValueError("lifecycle timestamp cannot precede order submission")
        if self.state.terminal:
            raise ValueError("terminal order cannot transition")
        event = LifecycleEvent(self.state.order.order_id, status, timestamp_ns, self.state.filled_quantity, self.state.remaining_quantity, reason, replacement_order_id)
        self.state = OrderState(self.state.order, status, self.state.filled_quantity, self.state.average_fill_price, reason if status == OrderStatus.REJECTED else self.state.reject_reason, self.state.time_in_force, self.state.events + (event,))
        return self.state

    def accept(self, timestamp_ns: int = 0) -> OrderState:
        if self.state.status != OrderStatus.SUBMITTED:
            raise ValueError("only SUBMITTED orders can be accepted")
        return self._transition(OrderStatus.ACCEPTED, timestamp_ns)

    def reject(self, reason: str, timestamp_ns: int = 0) -> OrderState:
        if not reason.strip():
            raise ValueError("reject reason is required")
        if self.state.status not in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED}:
            raise ValueError("only SUBMITTED or ACCEPTED orders can be rejected")
        return self._transition(OrderStatus.REJECTED, timestamp_ns, reason=reason)

    def cancel(self, timestamp_ns: int = 0, reason: str = "cancelled") -> OrderState:
        if self.state.terminal:
            raise ValueError("terminal order cannot be cancelled")
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only open orders can be cancelled")
        return self._transition(OrderStatus.CANCELLED, timestamp_ns, reason=reason)

    def expire(self, timestamp_ns: int = 0, reason: str = "expired") -> OrderState:
        if self.state.terminal:
            raise ValueError("terminal order cannot expire")
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only open orders can expire")
        return self._transition(OrderStatus.EXPIRED, timestamp_ns, reason=reason)

    def replace(self, replacement: SimOrder, timestamp_ns: int, reason: str = "replaced") -> OrderState:
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only open orders can be replaced")
        if replacement.instrument != self.state.order.instrument or replacement.side != self.state.order.side:
            raise ValueError("replacement must keep instrument and side")
        if replacement.submitted_at_ns < timestamp_ns:
            raise ValueError("replacement submission cannot precede replacement timestamp")
        return self._transition(OrderStatus.REPLACED, timestamp_ns, reason=reason, replacement_order_id=replacement.order_id)

    def apply_fill(self, fill: SimFill) -> OrderState:
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("order must be accepted before filling")
        if fill.order_id != self.state.order.order_id:
            raise ValueError("fill order_id does not match order")
        if fill.instrument != self.state.order.instrument or fill.side != self.state.order.side:
            raise ValueError("fill does not match order")
        if fill.filled_at_ns < self.state.order.submitted_at_ns:
            raise ValueError("fill timestamp cannot precede order submission")
        remaining = self.state.remaining_quantity
        if fill.quantity <= 0 or fill.quantity > remaining or fill.price <= 0:
            raise ValueError("invalid fill quantity or price")
        total_qty = self.state.filled_quantity + fill.quantity
        average = ((self.state.filled_quantity * self.state.average_fill_price) + (fill.quantity * fill.price)) / total_qty
        status = OrderStatus.FILLED if total_qty == self.state.order.quantity else OrderStatus.PARTIALLY_FILLED
        event = LifecycleEvent(self.state.order.order_id, status, fill.filled_at_ns, total_qty, self.state.order.quantity - total_qty)
        self.state = OrderState(self.state.order, status, total_qty, average, None, self.state.time_in_force, self.state.events + (event,))
        return self.state

    def to_fill(self, price: float, timestamp_ns: int, quantity: int | None = None, fee: float = 0.0) -> SimFill:
        remaining = self.state.remaining_quantity
        qty = remaining if quantity is None else quantity
        if qty <= 0 or qty > remaining:
            raise ValueError("invalid fill quantity")
        return SimFill(self.state.order.order_id, self.state.order.instrument, self.state.order.side, qty, price, timestamp_ns, fee)

    def export_state(self) -> Mapping[str, object]:
        """Serialize complete lifecycle state for durable checkpoint/resume."""
        order = self.state.order
        return {
            "order": {
                "order_id": order.order_id,
                "instrument": order.instrument,
                "side": order.side.value,
                "quantity": order.quantity,
                "order_type": order.order_type.value,
                "limit_price": order.limit_price,
                "stop_price": order.stop_price,
                "submitted_at_ns": order.submitted_at_ns,
                "queue_ahead_quantity": order.queue_ahead_quantity,
                "time_in_force": order.time_in_force.value,
            },
            "status": self.state.status.value,
            "filled_quantity": self.state.filled_quantity,
            "average_fill_price": self.state.average_fill_price,
            "reject_reason": self.state.reject_reason,
            "time_in_force": self.state.time_in_force.value,
            "events": [
                {"order_id": e.order_id, "status": e.status.value, "timestamp_ns": e.timestamp_ns,
                 "filled_quantity": e.filled_quantity, "remaining_quantity": e.remaining_quantity,
                 "reason": e.reason, "replacement_order_id": e.replacement_order_id}
                for e in self.state.events
            ],
        }

    @classmethod
    def restore_state(cls, raw: Mapping[str, object]) -> "OrderLifecycle":
        """Restore a lifecycle previously produced by export_state()."""
        order_raw = raw["order"]
        if not isinstance(order_raw, Mapping):
            raise ValueError("invalid lifecycle order state")
        order = SimOrder(
            order_id=str(order_raw["order_id"]), instrument=str(order_raw["instrument"]),
            side=ExecutionSide(str(order_raw["side"])), quantity=int(order_raw["quantity"]),
            order_type=OrderType(str(order_raw.get("order_type", OrderType.MARKET.value))),
            limit_price=order_raw.get("limit_price"), stop_price=order_raw.get("stop_price"),
            submitted_at_ns=int(order_raw.get("submitted_at_ns", 0)),
            queue_ahead_quantity=int(order_raw.get("queue_ahead_quantity", 0)),
            time_in_force=TimeInForce(str(order_raw.get("time_in_force", TimeInForce.DAY.value))),
        )
        lifecycle = cls(order)
        events = []
        for raw_event in raw.get("events", []):
            if not isinstance(raw_event, Mapping):
                raise ValueError("invalid lifecycle event")
            events.append(LifecycleEvent(
                order_id=str(raw_event["order_id"]), status=OrderStatus(str(raw_event["status"])),
                timestamp_ns=int(raw_event["timestamp_ns"]), filled_quantity=int(raw_event["filled_quantity"]),
                remaining_quantity=int(raw_event["remaining_quantity"]), reason=raw_event.get("reason"),
                replacement_order_id=raw_event.get("replacement_order_id"),
            ))
        lifecycle.state = OrderState(
            order=order, status=OrderStatus(str(raw["status"])),
            filled_quantity=int(raw.get("filled_quantity", 0)),
            average_fill_price=float(raw.get("average_fill_price", 0.0)),
            reject_reason=raw.get("reject_reason"),
            time_in_force=TimeInForce(str(raw.get("time_in_force", order.time_in_force.value))),
            events=tuple(events),
        )
        return lifecycle


def stop_triggered(order: SimOrder, observed_price: float) -> bool:
    """STOP triggers only from an observed positive point-in-time price."""
    if order.order_type != OrderType.STOP:
        return False
    if order.stop_price is None:
        raise ValueError("stop_price is required for STOP orders")
    if observed_price <= 0:
        raise ValueError("observed_price must be greater than zero")
    return observed_price >= order.stop_price if order.side == ExecutionSide.BUY else observed_price <= order.stop_price


def tif_after_execution(time_in_force: TimeInForce, remaining_quantity: int) -> OrderStatus | None:
    """Return the terminal action required for IOC/FOK residual quantity."""
    if remaining_quantity < 0:
        raise ValueError("remaining_quantity cannot be negative")
    if remaining_quantity == 0:
        return OrderStatus.FILLED
    if time_in_force == TimeInForce.IOC:
        return OrderStatus.CANCELLED
    if time_in_force == TimeInForce.FOK:
        return OrderStatus.REJECTED
    return None
