"""Deterministic open-order lifecycle for universal backtests."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
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

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("lifecycle event order_id is required")
        if not isinstance(self.status, OrderStatus):
            raise ValueError("invalid lifecycle event status")
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("lifecycle event timestamp must be a non-negative integer")
        for value, name in ((self.filled_quantity, "filled_quantity"), (self.remaining_quantity, "remaining_quantity")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("lifecycle event reason must be non-empty when provided")


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
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int):
            raise ValueError("lifecycle timestamp must be an integer")
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
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reject reason is required")
        if self.state.status not in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED}:
            raise ValueError("only SUBMITTED or ACCEPTED orders can be rejected")
        return self._transition(OrderStatus.REJECTED, timestamp_ns, reason=reason)

    def cancel(self, timestamp_ns: int = 0, reason: str = "cancelled") -> OrderState:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancel reason is required")
        if self.state.terminal:
            raise ValueError("terminal order cannot be cancelled")
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only open orders can be cancelled")
        return self._transition(OrderStatus.CANCELLED, timestamp_ns, reason=reason)

    def expire(self, timestamp_ns: int = 0, reason: str = "expired") -> OrderState:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("expire reason is required")
        if self.state.terminal:
            raise ValueError("terminal order cannot expire")
        if self.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only open orders can expire")
        return self._transition(OrderStatus.EXPIRED, timestamp_ns, reason=reason)

    def replace(self, replacement: SimOrder, timestamp_ns: int, reason: str = "replaced") -> OrderState:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("replace reason is required")
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
        order = self.state.order
        return {
            "order": {"order_id": order.order_id, "instrument": order.instrument, "side": order.side.value, "quantity": order.quantity, "order_type": order.order_type.value, "limit_price": order.limit_price, "stop_price": order.stop_price, "submitted_at_ns": order.submitted_at_ns, "queue_ahead_quantity": order.queue_ahead_quantity, "time_in_force": order.time_in_force.value},
            "status": self.state.status.value, "filled_quantity": self.state.filled_quantity, "average_fill_price": self.state.average_fill_price,
            "reject_reason": self.state.reject_reason, "time_in_force": self.state.time_in_force.value,
            "events": [{"order_id": e.order_id, "status": e.status.value, "timestamp_ns": e.timestamp_ns, "filled_quantity": e.filled_quantity, "remaining_quantity": e.remaining_quantity, "reason": e.reason, "replacement_order_id": e.replacement_order_id} for e in self.state.events],
        }

    @classmethod
    def restore_state(cls, raw: Mapping[str, object]) -> "OrderLifecycle":
        if not isinstance(raw, Mapping):
            raise ValueError("invalid lifecycle state")

        def strict_int(value: object, name: str, *, nonnegative: bool = False) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"invalid lifecycle {name}")
            if nonnegative and value < 0:
                raise ValueError(f"invalid lifecycle {name}")
            return value

        order_raw = raw.get("order")
        if not isinstance(order_raw, Mapping):
            raise ValueError("invalid lifecycle order state")
        order = SimOrder(order_id=str(order_raw["order_id"]), instrument=str(order_raw["instrument"]), side=ExecutionSide(str(order_raw["side"])), quantity=strict_int(order_raw["quantity"], "order quantity"), order_type=OrderType(str(order_raw.get("order_type", OrderType.MARKET.value))), limit_price=order_raw.get("limit_price"), stop_price=order_raw.get("stop_price"), submitted_at_ns=strict_int(order_raw.get("submitted_at_ns", 0), "submission timestamp", nonnegative=True), queue_ahead_quantity=strict_int(order_raw.get("queue_ahead_quantity", 0), "queue quantity", nonnegative=True), time_in_force=TimeInForce(str(order_raw.get("time_in_force", TimeInForce.DAY.value))))
        lifecycle = cls(order)
        raw_events = raw.get("events", [])
        if not isinstance(raw_events, (list, tuple)):
            raise ValueError("invalid lifecycle events")
        events: list[LifecycleEvent] = []
        for raw_event in raw_events:
            if not isinstance(raw_event, Mapping):
                raise ValueError("invalid lifecycle event")
            events.append(LifecycleEvent(order_id=str(raw_event["order_id"]), status=OrderStatus(str(raw_event["status"])), timestamp_ns=strict_int(raw_event["timestamp_ns"], "event timestamp", nonnegative=True), filled_quantity=strict_int(raw_event["filled_quantity"], "event filled quantity", nonnegative=True), remaining_quantity=strict_int(raw_event["remaining_quantity"], "event remaining quantity", nonnegative=True), reason=raw_event.get("reason"), replacement_order_id=raw_event.get("replacement_order_id")))

        status = OrderStatus(str(raw["status"]))
        filled_quantity = strict_int(raw.get("filled_quantity", 0), "filled quantity", nonnegative=True)
        average_fill_price = float(raw.get("average_fill_price", 0.0))
        time_in_force = TimeInForce(str(raw.get("time_in_force", order.time_in_force.value)))
        if filled_quantity > order.quantity:
            raise ValueError("invalid lifecycle filled quantity")
        if status == OrderStatus.FILLED and filled_quantity != order.quantity:
            raise ValueError("FILLED lifecycle must have full quantity")
        if status == OrderStatus.PARTIALLY_FILLED and not 0 < filled_quantity < order.quantity:
            raise ValueError("PARTIALLY_FILLED lifecycle must have residual quantity")
        if status == OrderStatus.SUBMITTED and filled_quantity != 0:
            raise ValueError("SUBMITTED lifecycle cannot contain fills")
        if status == OrderStatus.REJECTED and not isinstance(raw.get("reject_reason"), str):
            raise ValueError("REJECTED lifecycle requires reject_reason")
        if status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED} and filled_quantity > 0:
            if not isfinite(average_fill_price) or average_fill_price <= 0:
                raise ValueError("filled lifecycle requires positive average fill price")
        elif not isfinite(average_fill_price) or average_fill_price < 0:
            raise ValueError("invalid lifecycle average fill price")

        previous_timestamp = order.submitted_at_ns
        previous_filled = 0
        previous_status = OrderStatus.SUBMITTED
        for index, event in enumerate(events):
            if event.order_id != order.order_id:
                raise ValueError("lifecycle event order_id does not match order")
            if event.timestamp_ns < previous_timestamp:
                raise ValueError("lifecycle events must be chronological")
            if event.filled_quantity < previous_filled or event.filled_quantity > order.quantity:
                raise ValueError("lifecycle event filled quantities must be monotonic")
            if event.remaining_quantity != order.quantity - event.filled_quantity:
                raise ValueError("invalid lifecycle event quantities")
            if index == 0 and event.status not in {OrderStatus.ACCEPTED, OrderStatus.SUBMITTED}:
                raise ValueError("lifecycle must begin with acceptance/submission event")
            allowed = {
                OrderStatus.SUBMITTED: {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.REJECTED},
                OrderStatus.ACCEPTED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REPLACED, OrderStatus.REJECTED},
                OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REPLACED},
            }
            if event.status not in allowed.get(previous_status, set()):
                raise ValueError(f"illegal lifecycle transition: {previous_status.value} -> {event.status.value}")
            if event.status == OrderStatus.SUBMITTED and event.filled_quantity != 0:
                raise ValueError("SUBMITTED lifecycle event cannot contain fills")
            if event.status == OrderStatus.PARTIALLY_FILLED and not 0 < event.filled_quantity < order.quantity:
                raise ValueError("PARTIALLY_FILLED event must have residual quantity")
            if event.status == OrderStatus.FILLED and event.filled_quantity != order.quantity:
                raise ValueError("FILLED event must have full quantity")
            if event.status in {OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.REPLACED} and event.status == OrderStatus.REJECTED and not event.reason:
                raise ValueError("REJECTED lifecycle event requires reason")
            if event.status == OrderStatus.REPLACED and not event.replacement_order_id:
                raise ValueError("REPLACED lifecycle event requires replacement order id")
            if event.status == OrderStatus.ACCEPTED and event.filled_quantity != 0:
                raise ValueError("ACCEPTED lifecycle event cannot contain fills")
            previous_timestamp = event.timestamp_ns
            previous_filled = event.filled_quantity
            previous_status = event.status

        if events and events[-1].status != status:
            raise ValueError("lifecycle final event must match current status")
        if events and events[-1].filled_quantity != filled_quantity:
            raise ValueError("lifecycle final event must match filled quantity")
        if status == OrderStatus.REPLACED and not events:
            raise ValueError("REPLACED lifecycle requires a replacement event")
        lifecycle.state = OrderState(order=order, status=status, filled_quantity=filled_quantity, average_fill_price=average_fill_price, reject_reason=raw.get("reject_reason"), time_in_force=time_in_force, events=tuple(events))
        return lifecycle


def stop_triggered(order: SimOrder, observed_price: float) -> bool:
    if order.order_type != OrderType.STOP:
        return False
    if order.stop_price is None:
        raise ValueError("stop_price is required for STOP orders")
    if not isfinite(float(observed_price)) or observed_price <= 0:
        raise ValueError("observed_price must be finite and greater than zero")
    return observed_price >= order.stop_price if order.side == ExecutionSide.BUY else observed_price <= order.stop_price


def tif_after_execution(time_in_force: TimeInForce, remaining_quantity: int) -> OrderStatus | None:
    if remaining_quantity < 0:
        raise ValueError("remaining_quantity cannot be negative")
    if remaining_quantity == 0:
        return OrderStatus.FILLED
    if time_in_force == TimeInForce.IOC:
        return OrderStatus.CANCELLED
    if time_in_force == TimeInForce.FOK:
        return OrderStatus.REJECTED
    return None
