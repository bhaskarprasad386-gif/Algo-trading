"""Linked-order lifecycle primitives for bracket, OCO, and trailing exits."""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.backtesting.execution import ExecutionSide, OrderType, SimOrder


class LinkedOrderType(str, Enum):
    OCO = "OCO"
    BRACKET = "BRACKET"
    TRAILING = "TRAILING"


@dataclass(frozen=True)
class TrailingSpec:
    """Trailing distance in absolute price units or percentage of the reference."""

    distance: float
    percent: bool = False

    def __post_init__(self) -> None:
        if self.distance <= 0:
            raise ValueError("trailing distance must be greater than zero")


@dataclass(frozen=True)
class LinkedOrderGroup:
    group_id: str
    group_type: LinkedOrderType
    parent_order_id: str | None
    child_order_ids: tuple[str, ...]
    cancel_on_first_fill: bool = True
    trailing: TrailingSpec | None = None

    def __post_init__(self) -> None:
        if not self.group_id.strip():
            raise ValueError("group_id is required")
        if not self.child_order_ids:
            raise ValueError("linked order group requires child orders")
        if len(set(self.child_order_ids)) != len(self.child_order_ids):
            raise ValueError("child order ids must be unique")
        if self.group_type == LinkedOrderType.TRAILING and self.trailing is None:
            raise ValueError("trailing specification is required")


@dataclass(frozen=True)
class LinkedOrderState:
    group: LinkedOrderGroup
    active_order_ids: tuple[str, ...]
    filled_order_id: str | None = None
    peak_reference: float | None = None
    trail_stop_price: float | None = None
    cancelled_order_ids: tuple[str, ...] = ()


class LinkedOrderManager:
    """Deterministic state machine for linked exits.

    The manager does not manufacture fills. It only activates/cancels/reprices
    linked orders from observed fills and market prices; the execution engine
    remains the sole authority for actual fills.
    """

    def __init__(self) -> None:
        self._states: dict[str, LinkedOrderState] = {}
        self._orders: dict[str, SimOrder] = {}

    @property
    def states(self) -> Mapping[str, LinkedOrderState]:
        return dict(self._states)

    def register_oco(self, group_id: str, orders: tuple[SimOrder, ...]) -> LinkedOrderGroup:
        if len(orders) < 2:
            raise ValueError("OCO requires at least two orders")
        group = LinkedOrderGroup(group_id, LinkedOrderType.OCO, None, tuple(o.order_id for o in orders))
        self._register(group, orders, active=True)
        return group

    def register_bracket(self, group_id: str, parent: SimOrder, take_profit: SimOrder, stop_loss: SimOrder) -> LinkedOrderGroup:
        if take_profit.side != stop_loss.side:
            raise ValueError("bracket children must use the same exit side")
        if take_profit.instrument != parent.instrument or stop_loss.instrument != parent.instrument:
            raise ValueError("bracket children must match parent instrument")
        if take_profit.side == parent.side or stop_loss.side == parent.side:
            raise ValueError("bracket children must be opposite to parent side")
        group = LinkedOrderGroup(group_id, LinkedOrderType.BRACKET, parent.order_id,
                                 (take_profit.order_id, stop_loss.order_id))
        self._orders[parent.order_id] = parent
        self._orders[take_profit.order_id] = take_profit
        self._orders[stop_loss.order_id] = stop_loss
        self._states[group_id] = LinkedOrderState(group, ())
        return group

    def register_trailing(self, group_id: str, order: SimOrder, spec: TrailingSpec) -> LinkedOrderGroup:
        if order.order_type != OrderType.STOP:
            raise ValueError("trailing order must be STOP")
        group = LinkedOrderGroup(group_id, LinkedOrderType.TRAILING, None, (order.order_id,), trailing=spec)
        self._register(group, (order,), active=False)
        return group

    def _register(self, group: LinkedOrderGroup, orders: tuple[SimOrder, ...], *, active: bool) -> None:
        for order in orders:
            if order.order_id in self._orders:
                raise ValueError(f"duplicate linked order id: {order.order_id}")
            self._orders[order.order_id] = order
        self._states[group.group_id] = LinkedOrderState(
            group, tuple(o.order_id for o in orders) if active else ()
        )

    def activate_bracket(self, group_id: str, parent_fill_quantity: int, timestamp_ns: int) -> tuple[SimOrder, ...]:
        state = self._require(group_id)
        if state.group.group_type != LinkedOrderType.BRACKET:
            raise ValueError("group is not a bracket")
        if parent_fill_quantity <= 0:
            raise ValueError("parent fill quantity must be positive")
        if timestamp_ns < 0:
            raise ValueError("timestamp cannot be negative")
        if state.active_order_ids:
            return tuple(self._orders[i] for i in state.active_order_ids)
        self._states[group_id] = LinkedOrderState(state.group, state.group.child_order_ids)
        return tuple(self._orders[i] for i in state.group.child_order_ids)

    def on_fill(self, group_id: str, filled_order_id: str, timestamp_ns: int) -> tuple[str, ...]:
        state = self._require(group_id)
        if filled_order_id not in state.group.child_order_ids:
            raise ValueError("filled order is not a child of this group")
        if timestamp_ns < 0:
            raise ValueError("timestamp cannot be negative")
        if state.filled_order_id is not None:
            return state.cancelled_order_ids
        cancelled = tuple(i for i in state.active_order_ids if i != filled_order_id)
        self._states[group_id] = LinkedOrderState(
            state.group,
            (filled_order_id,),
            filled_order_id=filled_order_id,
            peak_reference=state.peak_reference,
            trail_stop_price=state.trail_stop_price,
            cancelled_order_ids=cancelled,
        )
        return cancelled

    def update_trailing(self, group_id: str, observed_price: float, timestamp_ns: int) -> SimOrder:
        state = self._require(group_id)
        if state.group.group_type != LinkedOrderType.TRAILING:
            raise ValueError("group is not trailing")
        if observed_price <= 0 or timestamp_ns < 0:
            raise ValueError("observed price and timestamp must be positive")
        order = self._orders[state.group.child_order_ids[0]]
        spec = state.group.trailing
        assert spec is not None
        reference = observed_price if state.peak_reference is None else max(state.peak_reference, observed_price) if order.side == ExecutionSide.SELL else min(state.peak_reference, observed_price)
        if order.side == ExecutionSide.SELL:
            stop = reference * (1 - spec.distance / 100) if spec.percent else reference - spec.distance
        else:
            stop = reference * (1 + spec.distance / 100) if spec.percent else reference + spec.distance
        updated = SimOrder(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            order_type=OrderType.STOP,
            limit_price=order.limit_price,
            stop_price=stop,
            submitted_at_ns=order.submitted_at_ns,
            queue_ahead_quantity=order.queue_ahead_quantity,
            time_in_force=order.time_in_force,
        )
        self._orders[order.order_id] = updated
        self._states[group_id] = LinkedOrderState(state.group, state.active_order_ids,
            filled_order_id=state.filled_order_id, peak_reference=reference,
            trail_stop_price=stop, cancelled_order_ids=state.cancelled_order_ids)
        return updated

    def active_orders(self, group_id: str) -> tuple[SimOrder, ...]:
        state = self._require(group_id)
        return tuple(self._orders[i] for i in state.active_order_ids if i not in state.cancelled_order_ids)

    def export_state(self) -> Mapping[str, object]:
        return {
            gid: {
                "group_type": state.group.group_type.value,
                "parent_order_id": state.group.parent_order_id,
                "child_order_ids": list(state.group.child_order_ids),
                "active_order_ids": list(state.active_order_ids),
                "filled_order_id": state.filled_order_id,
                "peak_reference": state.peak_reference,
                "trail_stop_price": state.trail_stop_price,
                "cancelled_order_ids": list(state.cancelled_order_ids),
            }
            for gid, state in self._states.items()
        }

    def _require(self, group_id: str) -> LinkedOrderState:
        try:
            return self._states[group_id]
        except KeyError as exc:
            raise KeyError(f"linked order group not found: {group_id}") from exc
