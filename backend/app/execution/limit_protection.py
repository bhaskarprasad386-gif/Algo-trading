"""Deterministic smart-limit and slippage protection primitives."""

from dataclasses import dataclass
from enum import Enum
import math


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class SlippageConfig:
    max_slippage_pct: float = 0.005
    tick_size: float = 0.05

    def __post_init__(self) -> None:
        if self.max_slippage_pct < 0:
            raise ValueError("max_slippage_pct cannot be negative")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")


def protected_limit_price(
    reference_price: float,
    side: OrderSide,
    config: SlippageConfig | None = None,
) -> float:
    """Return a conservative tick-aligned limit inside the slippage budget."""
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")

    active = config or SlippageConfig()
    raw_limit = (
        reference_price * (1.0 + active.max_slippage_pct)
        if side is OrderSide.BUY
        else reference_price * (1.0 - active.max_slippage_pct)
    )

    # BUY must round down and SELL must round up, while preserving an exact
    # slippage boundary when it already falls on a valid tick.  The epsilon
    # only neutralizes harmless floating-point representation noise.
    ticks = raw_limit / active.tick_size
    epsilon = 1e-12

    if side is OrderSide.BUY:
        aligned_ticks = math.floor(ticks + epsilon)
    else:
        aligned_ticks = math.ceil(ticks - epsilon)

    return aligned_ticks * active.tick_size


def within_slippage(
    reference_price: float,
    proposed_price: float,
    side: OrderSide,
    max_slippage_pct: float,
) -> bool:
    """Check whether a proposed price stays inside the side-specific slippage cap."""
    if reference_price <= 0 or proposed_price <= 0:
        raise ValueError("prices must be positive")
    if max_slippage_pct < 0:
        raise ValueError("max_slippage_pct cannot be negative")

    tolerance = max(abs(reference_price) * 1e-12, 1e-12)
    if side is OrderSide.BUY:
        return proposed_price <= reference_price * (1.0 + max_slippage_pct) + tolerance
    return proposed_price >= reference_price * (1.0 - max_slippage_pct) - tolerance
