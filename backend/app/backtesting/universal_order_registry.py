"""Order/lifecycle/queue registry for universal backtest execution.

The registry owns order state consistency only. Portfolio accounting, market state,
execution matching, and strategy decisions remain outside this module.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from app.backtesting.execution import ExecutionResult, QueueEvidence, SimFill, SimOrder, TimeInForce
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus
from app.backtesting.queue_lifecycle import QueueLifecycleState


@dataclass(frozen=True)
class RegistryExecutionOutcome:
    """State changes produced by one validated execution result."""

    order_id: str
    status: OrderStatus
    filled_quantity: int
    remaining_quantity: int
    released_reservation: float


class UniversalOrderRegistry:
    """Authoritative in-memory registry for Universal Engine open orders."""

    def __init__(self) -> None:
        self._orders: dict[str, SimOrder] = {}
        self._lifecycles: dict[str, OrderLifecycle] = {}
        self._queue: dict[str, QueueLifecycleState] = {}
        self._reservations: dict[str, float] = {}

    @staticmethod
    def _validate_reservation(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("reservation must be finite and non-negative")
        value = float(value)
        if not isfinite(value) or value < 0:
            raise ValueError("reservation must be finite and non-negative")
        return value

    def submit(self, order: SimOrder, reservation: float = 0.0) -> None:
        """Register and accept a new open order atomically."""
        if not isinstance(order, SimOrder):
            raise TypeError("order must be a SimOrder")
        reservation = self._validate_reservation(reservation)
        if order.order_id in self._lifecycles:
            raise ValueError("order_id is already in use")

        lifecycle = OrderLifecycle(order)
        lifecycle.accept(order.submitted_at_ns)
        queue = QueueLifecycleState(order.queue_ahead_quantity)

        self._lifecycles[order.order_id] = lifecycle
        self._orders[order.order_id] = order
        self._queue[order.order_id] = queue
        if reservation > 0:
            self._reservations[order.order_id] = reservation

    def get(self, order_id: str) -> SimOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"open order not found: {order_id}") from exc

    def lifecycle(self, order_id: str) -> OrderLifecycle:
        try:
            return self._lifecycles[order_id]
        except KeyError as exc:
            raise KeyError(f"order not found: {order_id}") from exc

    def queue_state(self, order_id: str) -> QueueLifecycleState:
        try:
            return self._queue[order_id]
        except KeyError as exc:
            raise KeyError(f"open order not found: {order_id}") from exc

    def reservation(self, order_id: str) -> float:
        return self._reservations.get(order_id, 0.0)

    def open_orders(self) -> tuple[SimOrder, ...]:
        return tuple(self._orders.values())

    def effective_order(self, order_id: str) -> SimOrder:
        """Return the execution order carrying the current dynamic queue."""
        order = self.get(order_id)
        queue = self.queue_state(order_id)
        return SimOrder(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=self.lifecycle(order_id).state.remaining_quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            submitted_at_ns=order.submitted_at_ns,
            queue_ahead_quantity=queue.queue_ahead_quantity,
            time_in_force=order.time_in_force,
        )

    def advance_queue(self, order_id: str, evidence: QueueEvidence) -> QueueLifecycleState:
        state = self.queue_state(order_id)
        updated = state.advance(evidence)
        self._queue[order_id] = updated
        return updated

    def apply_execution(
        self,
        order_id: str,
        result: ExecutionResult,
        timestamp_ns: int,
    ) -> RegistryExecutionOutcome:
        """Apply one execution result after validating all fills up front."""
        lifecycle = self.lifecycle(order_id)
        order = self.get(order_id)
        if not isinstance(result, ExecutionResult):
            raise TypeError("result must be an ExecutionResult")
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < order.submitted_at_ns:
            raise ValueError("execution timestamp must be a non-negative integer after submission")

        fills = tuple(result.fills)
        available_quantity = lifecycle.state.remaining_quantity
        total_fill_quantity = 0
        previous_filled = lifecycle.state.filled_quantity
        for fill in fills:
            if not isinstance(fill, SimFill):
                raise TypeError("execution fills must be SimFill instances")
            if fill.order_id != order_id:
                raise ValueError("execution fill order_id does not match order")
            if fill.instrument != order.instrument or fill.side != order.side:
                raise ValueError("execution fill does not match order")
            if fill.filled_at_ns < order.submitted_at_ns:
                raise ValueError("execution fill timestamp cannot precede submission")
            if fill.quantity <= 0 or fill.quantity > available_quantity - total_fill_quantity:
                raise ValueError("invalid execution fill quantity")
            total_fill_quantity += fill.quantity
        final_remaining = lifecycle.state.remaining_quantity - total_fill_quantity
        if final_remaining < 0:
            raise ValueError("execution fills exceed remaining order quantity")
        if result.remaining_quantity != final_remaining:
            raise ValueError("execution result remaining quantity is inconsistent")

        # FOK rejection is an execution rejection, not a lifecycle fill.
        if result.rejected:
            if fills:
                raise ValueError("rejected execution cannot contain fills")
            if result.remaining_quantity != lifecycle.state.remaining_quantity:
                raise ValueError("rejected execution cannot change remaining quantity")
            if lifecycle.state.status in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED}:
                lifecycle.reject(result.reason or "execution rejected", timestamp_ns)
            return RegistryExecutionOutcome(
                order_id, lifecycle.state.status, previous_filled, lifecycle.state.remaining_quantity, 0.0
            )

        # Lifecycle mutation starts only after every fill has been validated.
        for fill in fills:
            lifecycle.apply_fill(fill)

        released = 0.0
        old_reservation = self._reservations.get(order_id, 0.0)
        if old_reservation:
            original_quantity = order.quantity
            filled_delta = total_fill_quantity
            released = old_reservation * filled_delta / original_quantity
            remaining_reservation = max(0.0, old_reservation - released)
        else:
            remaining_reservation = 0.0

        if lifecycle.state.remaining_quantity == 0:
            if old_reservation:
                released = old_reservation
            self._orders.pop(order_id, None)
            self._queue[order_id] = self._queue[order_id].cancel()
            self._reservations.pop(order_id, None)
        elif order.time_in_force == TimeInForce.IOC:
            lifecycle.cancel(timestamp_ns, result.reason or "IOC remainder cancelled")
            self._orders.pop(order_id, None)
            self._queue[order_id] = self._queue[order_id].cancel()
            self._reservations.pop(order_id, None)
            released = old_reservation
        else:
            if old_reservation:
                self._reservations[order_id] = remaining_reservation

        return RegistryExecutionOutcome(
            order_id,
            lifecycle.state.status,
            lifecycle.state.filled_quantity,
            lifecycle.state.remaining_quantity,
            released,
        )

    def cancel(self, order_id: str, timestamp_ns: int, reason: str = "cancelled") -> float:
        lifecycle = self.lifecycle(order_id)
        self.get(order_id)
        lifecycle.cancel(timestamp_ns, reason)
        released = self._reservations.pop(order_id, 0.0)
        self._orders.pop(order_id, None)
        self._queue[order_id] = self._queue[order_id].cancel()
        return released

    def replace(
        self,
        order_id: str,
        replacement: SimOrder,
        timestamp_ns: int,
        reservation: float | None = None,
    ) -> None:
        """Replace an open order and start a fresh queue generation."""
        old = self.get(order_id)
        lifecycle = self.lifecycle(order_id)
        if not isinstance(replacement, SimOrder):
            raise TypeError("replacement must be a SimOrder")
        if replacement.order_id in self._lifecycles:
            raise ValueError("replacement order_id is already in use")
        if replacement.instrument != old.instrument or replacement.side != old.side:
            raise ValueError("replacement must keep instrument and side")
        if replacement.submitted_at_ns < timestamp_ns:
            raise ValueError("replacement submission cannot precede replacement timestamp")

        remaining = lifecycle.state.remaining_quantity
        if replacement.quantity != remaining:
            raise ValueError("replacement quantity must equal remaining quantity")

        new_reservation = self.reservation(order_id) if reservation is None else self._validate_reservation(reservation)
        old_queue = self._queue[order_id]

        # All validation is complete before mutating either order.
        lifecycle.replace(replacement, timestamp_ns)
        new_lifecycle = OrderLifecycle(replacement)
        new_lifecycle.accept(replacement.submitted_at_ns)
        new_queue = old_queue.cancel().reinsert(replacement.queue_ahead_quantity)

        self._orders.pop(order_id, None)
        self._queue[order_id] = old_queue.cancel()
        self._reservations.pop(order_id, None)

        self._lifecycles[replacement.order_id] = new_lifecycle
        self._orders[replacement.order_id] = replacement
        self._queue[replacement.order_id] = new_queue
        if new_reservation > 0:
            self._reservations[replacement.order_id] = new_reservation

    def export_state(self) -> Mapping[str, object]:
        return {
            "orders": {
                order_id: {
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
                }
                for order_id, order in self._orders.items()
            },
            "lifecycles": {order_id: lifecycle.export_state() for order_id, lifecycle in self._lifecycles.items()},
            "queue": {
                order_id: {
                    "queue_ahead_quantity": state.queue_ahead_quantity,
                    "generation": state.generation,
                    "resting": state.resting,
                }
                for order_id, state in self._queue.items()
            },
            "reservations": dict(self._reservations),
        }

    @classmethod
    def restore_state(cls, raw: Mapping[str, object]) -> "UniversalOrderRegistry":
        if not isinstance(raw, Mapping):
            raise ValueError("invalid order registry state")
        registry = cls()
        raw_orders = raw.get("orders", {})
        raw_lifecycles = raw.get("lifecycles", {})
        raw_queue = raw.get("queue", {})
        raw_reservations = raw.get("reservations", {})
        if not all(isinstance(value, Mapping) for value in (raw_orders, raw_lifecycles, raw_queue, raw_reservations)):
            raise ValueError("invalid order registry state maps")

        lifecycles: dict[str, OrderLifecycle] = {}
        for order_id, value in raw_lifecycles.items():
            lifecycle = OrderLifecycle.restore_state(value)
            if lifecycle.state.order.order_id != str(order_id):
                raise ValueError("lifecycle order_id does not match state key")
            lifecycles[str(order_id)] = lifecycle

        orders: dict[str, SimOrder] = {}
        queue: dict[str, QueueLifecycleState] = {}
        reservations: dict[str, float] = {}

        for order_id, value in raw_orders.items():
            lifecycle = lifecycles.get(str(order_id))
            if lifecycle is None:
                raise ValueError("open order requires lifecycle state")
            if lifecycle.state.terminal:
                raise ValueError("open order cannot have terminal lifecycle")
            if not isinstance(value, Mapping):
                raise ValueError("invalid open order state")
            expected = lifecycle.state.order
            if str(value.get("order_id")) != expected.order_id:
                raise ValueError("open order id does not match lifecycle")
            if expected.quantity != int(value.get("quantity")):
                raise ValueError("open order quantity does not match lifecycle")
            if expected.instrument != str(value.get("instrument")):
                raise ValueError("open order instrument does not match lifecycle")
            orders[str(order_id)] = expected

            raw_queue_state = raw_queue.get(str(order_id))
            if not isinstance(raw_queue_state, Mapping):
                raise ValueError("open order requires queue state")
            state = QueueLifecycleState(
                int(raw_queue_state.get("queue_ahead_quantity")),
                int(raw_queue_state.get("generation", 0)),
                bool(raw_queue_state.get("resting", True)),
            )
            if not state.resting:
                raise ValueError("open order queue must be resting")
            queue[str(order_id)] = state

        for order_id, value in raw_queue.items():
            if str(order_id) not in orders and str(order_id) not in lifecycles:
                raise ValueError("queue state references unknown order")

        for order_id, value in raw_reservations.items():
            reservation = cls._validate_reservation(value)
            if str(order_id) not in orders:
                raise ValueError("reservation must belong to an open order")
            if reservation > 0:
                reservations[str(order_id)] = reservation

        registry._lifecycles = lifecycles
        registry._orders = orders
        registry._queue = queue
        registry._reservations = reservations
        return registry
