"""Normalized, account-scoped broker order events.

This module is deliberately side-effect free. It defines callback identity and
payload invariants for adapters; durable deduplication must still be enforced
by the persistence layer before an event changes execution state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrokerOrderEventStatus(str, Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrokerOrderEvent:
    """One normalized broker callback, scoped to one broker account."""

    broker: str
    account_id: str
    event_id: str
    broker_order_id: str
    status: BrokerOrderEventStatus
    cumulative_filled_quantity: int = 0
    fill_id: str | None = None
    fill_quantity: int | None = None
    average_fill_price: float | None = None
    occurred_at_ns: int = 0

    def __post_init__(self) -> None:
        for field_name in ("broker", "account_id", "event_id", "broker_order_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} cannot be empty")
        if not isinstance(self.status, BrokerOrderEventStatus):
            raise ValueError("status must be a BrokerOrderEventStatus")
        if self.cumulative_filled_quantity < 0:
            raise ValueError("cumulative_filled_quantity cannot be negative")
        if self.fill_quantity is not None and self.fill_quantity <= 0:
            raise ValueError("fill_quantity must be positive")
        if self.fill_id is not None and not self.fill_id.strip():
            raise ValueError("fill_id cannot be empty")
        if (self.fill_id is None) != (self.fill_quantity is None):
            raise ValueError("fill_id and fill_quantity must be supplied together")
        if self.average_fill_price is not None and self.average_fill_price <= 0:
            raise ValueError("average_fill_price must be positive")
        if self.occurred_at_ns < 0:
            raise ValueError("occurred_at_ns cannot be negative")
        if (
            self.status is BrokerOrderEventStatus.PARTIALLY_FILLED
            and self.cumulative_filled_quantity <= 0
        ):
            raise ValueError("PARTIALLY_FILLED requires a positive cumulative fill")
        if (
            self.status is BrokerOrderEventStatus.FILLED
            and self.cumulative_filled_quantity <= 0
        ):
            raise ValueError("FILLED requires a positive cumulative fill")


def broker_event_identity(event: BrokerOrderEvent) -> tuple[str, str, str]:
    """Stable callback identity; account scope prevents cross-account collisions."""
    return (event.broker, event.account_id, event.event_id)


def broker_fill_identity(event: BrokerOrderEvent) -> tuple[str, str, str] | None:
    """Stable fill identity when the broker supplies a fill/trade identifier."""
    if event.fill_id is None:
        return None
    return (event.broker, event.account_id, event.fill_id)


def assert_same_event_payload(
    existing: BrokerOrderEvent, incoming: BrokerOrderEvent
) -> None:
    """Allow exact callback replay; reject identity reuse with changed payload."""
    if broker_event_identity(existing) != broker_event_identity(incoming):
        raise ValueError("broker event identity mismatch")
    if existing != incoming:
        raise ValueError("broker event identity reused with changed payload")


def validate_event_scope(
    event: BrokerOrderEvent, *, broker: str, account_id: str
) -> None:
    """Fail closed when an event is delivered to the wrong adapter/account."""
    if event.broker != broker or event.account_id != account_id:
        raise ValueError("broker order event account scope mismatch")
