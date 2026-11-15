"""Pure, side-effect-free execution lifecycle contracts.

These value objects define account-scoped intent and leg invariants. They do
not submit orders, access a database, or enable Live execution.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class IntentStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    RISK_CHECK = "RISK_CHECK"
    APPROVED = "APPROVED"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    COMPLETED = "COMPLETED"


_ALLOWED_TRANSITIONS: dict[IntentStatus, frozenset[IntentStatus]] = {
    IntentStatus.CREATED: frozenset({IntentStatus.VALIDATING}),
    IntentStatus.VALIDATING: frozenset({IntentStatus.REJECTED, IntentStatus.RISK_CHECK}),
    IntentStatus.RISK_CHECK: frozenset({IntentStatus.REJECTED, IntentStatus.APPROVED}),
    IntentStatus.APPROVED: frozenset({IntentStatus.RESERVED}),
    IntentStatus.RESERVED: frozenset({IntentStatus.SUBMITTING}),
    IntentStatus.SUBMITTING: frozenset({
        IntentStatus.REJECTED, IntentStatus.WORKING, IntentStatus.UNKNOWN,
    }),
    IntentStatus.WORKING: frozenset({
        IntentStatus.PARTIALLY_FILLED, IntentStatus.FILLED,
        IntentStatus.CANCEL_PENDING, IntentStatus.UNKNOWN,
    }),
    IntentStatus.PARTIALLY_FILLED: frozenset({
        IntentStatus.WORKING, IntentStatus.FILLED,
        IntentStatus.CANCEL_PENDING, IntentStatus.UNKNOWN,
    }),
    IntentStatus.CANCEL_PENDING: frozenset({
        IntentStatus.CANCELLED, IntentStatus.PARTIALLY_FILLED,
        IntentStatus.FILLED, IntentStatus.UNKNOWN,
    }),
    IntentStatus.UNKNOWN: frozenset({IntentStatus.RECONCILIATION_REQUIRED}),
    IntentStatus.RECONCILIATION_REQUIRED: frozenset({
        IntentStatus.WORKING, IntentStatus.PARTIALLY_FILLED,
        IntentStatus.FILLED, IntentStatus.CANCELLED,
        IntentStatus.MANUAL_REVIEW,
    }),
    IntentStatus.FILLED: frozenset({IntentStatus.COMPLETED}),
    IntentStatus.CANCELLED: frozenset({IntentStatus.COMPLETED}),
    IntentStatus.REJECTED: frozenset(),
    IntentStatus.MANUAL_REVIEW: frozenset(),
    IntentStatus.COMPLETED: frozenset(),
}


@dataclass(frozen=True)
class ExecutionIntent:
    """Immutable identity and current lifecycle state for one basket request."""

    intent_id: str
    account_id: str
    idempotency_key: str
    correlation_id: str
    mode: ExecutionMode
    status: IntentStatus = IntentStatus.CREATED
    created_at_ns: int = 0

    def __post_init__(self) -> None:
        for name in ("intent_id", "account_id", "idempotency_key", "correlation_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be empty")
        if self.created_at_ns < 0:
            raise ValueError("created_at_ns cannot be negative")

    def transition(self, target: IntentStatus) -> "ExecutionIntent":
        """Return a new intent after validating the explicit transition graph."""
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"invalid intent transition: {self.status.value} -> {target.value}")
        return replace(self, status=target)


@dataclass(frozen=True)
class ExecutionLeg:
    """Immutable requested leg; quantities are expressed in broker-valid units."""

    leg_id: str
    instrument: str
    side: OrderSide
    quantity: int
    order_type: str = "LIMIT"
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.leg_id.strip() or not self.instrument.strip():
            raise ValueError("leg_id and instrument cannot be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type not in {"LIMIT", "MARKET"}:
            raise ValueError("unsupported normalized order_type")
        if self.order_type == "LIMIT" and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("LIMIT legs require a positive limit_price")
        if self.order_type == "MARKET" and self.limit_price is not None:
            raise ValueError("MARKET legs cannot carry a limit_price")


@dataclass(frozen=True)
class LegProgress:
    """Normalized progress snapshot; it does not imply exchange atomicity."""

    leg_id: str
    requested_quantity: int
    filled_quantity: int = 0
    status: str = "PENDING"

    def __post_init__(self) -> None:
        if not self.leg_id.strip():
            raise ValueError("leg_id cannot be empty")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        if not 0 <= self.filled_quantity <= self.requested_quantity:
            raise ValueError("filled_quantity must be between zero and requested_quantity")
        if self.status not in {
            "PENDING", "WORKING", "PARTIALLY_FILLED", "FILLED",
            "CANCEL_PENDING", "CANCELLED", "REJECTED", "UNKNOWN",
        }:
            raise ValueError("unsupported leg status")
        if self.status == "FILLED" and self.filled_quantity != self.requested_quantity:
            raise ValueError("FILLED requires the full requested quantity")
        if self.status == "PARTIALLY_FILLED" and not 0 < self.filled_quantity < self.requested_quantity:
            raise ValueError("PARTIALLY_FILLED requires a non-zero incomplete quantity")


def validate_basket_legs(legs: tuple[ExecutionLeg, ...]) -> None:
    """Validate basket identity and equal quantities before risk reservation."""
    if len(legs) < 2:
        raise ValueError("a multi-leg basket requires at least two legs")
    ids = [leg.leg_id for leg in legs]
    if len(ids) != len(set(ids)):
        raise ValueError("leg_id values must be unique within a basket")
    quantities = {leg.quantity for leg in legs}
    if len(quantities) != 1:
        raise ValueError("all basket legs must have equal quantities")
