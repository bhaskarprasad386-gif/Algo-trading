"""Deterministic multi-leg execution safeguards."""

from dataclasses import dataclass
from enum import Enum


class WatchdogAction(str, Enum):
    WAIT = "wait"
    ROLLBACK = "rollback"
    SQUARE_OFF = "square_off"


@dataclass(frozen=True)
class LegFill:
    leg_id: str
    requested_quantity: float
    filled_quantity: float

    def __post_init__(self) -> None:
        if not self.leg_id.strip():
            raise ValueError("leg_id cannot be empty")
        if self.requested_quantity <= 0 or self.filled_quantity < 0:
            raise ValueError("quantities are invalid")
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity cannot exceed requested_quantity")

    @property
    def remaining_quantity(self) -> float:
        return self.requested_quantity - self.filled_quantity

    @property
    def is_complete(self) -> bool:
        return self.remaining_quantity == 0


@dataclass(frozen=True)
class WatchdogPolicy:
    timeout_ms: int = 1000

    def __post_init__(self) -> None:
        if self.timeout_ms < 1:
            raise ValueError("timeout_ms must be at least 1")


@dataclass(frozen=True)
class WatchdogDecision:
    action: WatchdogAction
    timed_out: bool


def evaluate_watchdog(elapsed_ms: int, complete: bool, policy: WatchdogPolicy) -> WatchdogDecision:
    if elapsed_ms < 0:
        raise ValueError("elapsed_ms cannot be negative")
    if complete:
        return WatchdogDecision(WatchdogAction.WAIT, False)
    if elapsed_ms < policy.timeout_ms:
        return WatchdogDecision(WatchdogAction.WAIT, False)
    return WatchdogDecision(WatchdogAction.ROLLBACK, True)


def rollback_required(fills: tuple[LegFill, ...]) -> bool:
    """Require rollback when a multi-leg order has partial/incomplete fills."""
    return bool(fills) and any(not fill.is_complete for fill in fills)


def square_off_required(kill_switch_active: bool, open_legs: int) -> bool:
    """Signal square-off when safety shutdown is active and positions remain open."""
    if open_legs < 0:
        raise ValueError("open_legs cannot be negative")
    return kill_switch_active and open_legs > 0
