"""Conservative broker order-status normalization for reconciliation.

This module classifies provider evidence only. It does not mutate orders,
retry requests, or authorize an execution/recovery action.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


SUPPORTED_BROKERS = frozenset({"angel_one", "zerodha", "fyers", "shoonya", "mstock"})


class NormalizedOrderState(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PARTIALLY_FILLED_CANCELLED = "PARTIALLY_FILLED_CANCELLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OrderStatusEvidence:
    broker: str
    raw_status: str
    state: NormalizedOrderState
    quantity: int
    filled_quantity: int
    remaining_quantity: int
    terminal: bool
    requires_reconciliation: bool
    status_conflict: bool


# Deliberately conservative textual aliases shared across supported provider
# responses. Numeric/vendor-specific codes are not guessed; they stay UNKNOWN.
_STATUS_ALIASES = {
    "OPEN": "OPEN",
    "NEW": "OPEN",
    "PENDING": "OPEN",
    "RECEIVED": "OPEN",
    "PUT ORDER REQ RECEIVED": "OPEN",
    "VALIDATION PENDING": "OPEN",
    "TRIGGER PENDING": "OPEN",
    "PARTIALLY FILLED": "PARTIALLY_FILLED",
    "PARTIAL FILLED": "PARTIALLY_FILLED",
    "PARTIAL": "PARTIALLY_FILLED",
    "COMPLETE": "FILLED",
    "COMPLETED": "FILLED",
    "FILLED": "FILLED",
    "TRADED": "FILLED",
    "CANCELLED": "CANCELLED",
    "CANCELED": "CANCELLED",
    "CANCELLED BY USER": "CANCELLED",
    "CANCELED BY USER": "CANCELLED",
    "REJECTED": "REJECTED",
}


def normalize_order_status(
    *,
    broker: str,
    raw_status: object,
    quantity: int,
    filled_quantity: int,
) -> OrderStatusEvidence:
    """Normalize known textual states while preserving fill/cancel race evidence.

    Any contradictory terminal status and non-zero fill is marked for manual
    reconciliation. Unknown vendor codes are never promoted to OPEN or FILLED.
    """
    key = str(broker or "").strip().lower()
    if key not in SUPPORTED_BROKERS:
        raise ValueError(f"unsupported broker for order-status normalization: {key}")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("order quantity must be a positive integer")
    if (
        isinstance(filled_quantity, bool)
        or not isinstance(filled_quantity, int)
        or filled_quantity < 0
        or filled_quantity > quantity
    ):
        raise ValueError("filled quantity must be an integer between zero and order quantity")

    raw = "" if raw_status is None else str(raw_status).strip()
    token = " ".join(raw.upper().replace("_", " ").replace("-", " ").split())
    mapped = _STATUS_ALIASES.get(token, "UNKNOWN")
    remaining = quantity - filled_quantity
    conflict = False
    requires_reconciliation = False

    if filled_quantity == quantity:
        state = NormalizedOrderState.FILLED
        conflict = mapped in {"CANCELLED", "REJECTED", "OPEN", "UNKNOWN", "PARTIALLY_FILLED"}
        requires_reconciliation = conflict
        terminal = True
    elif filled_quantity > 0:
        if mapped == "CANCELLED":
            state = NormalizedOrderState.PARTIALLY_FILLED_CANCELLED
            terminal = True
            requires_reconciliation = True
        else:
            state = NormalizedOrderState.PARTIALLY_FILLED
            terminal = mapped == "REJECTED"
            conflict = mapped in {"REJECTED", "FILLED", "UNKNOWN"}
            requires_reconciliation = conflict or mapped == "PARTIALLY_FILLED"
    else:
        state = NormalizedOrderState(mapped)
        terminal = state in {
            NormalizedOrderState.FILLED,
            NormalizedOrderState.CANCELLED,
            NormalizedOrderState.REJECTED,
        }
        if state == NormalizedOrderState.FILLED:
            # Provider says filled but no fill quantity is visible yet.
            requires_reconciliation = True
        elif state == NormalizedOrderState.UNKNOWN:
            requires_reconciliation = True

    return OrderStatusEvidence(
        broker=key,
        raw_status=raw,
        state=state,
        quantity=quantity,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining,
        terminal=terminal,
        requires_reconciliation=requires_reconciliation,
        status_conflict=conflict,
    )
