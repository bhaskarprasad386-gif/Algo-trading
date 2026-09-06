"""Strategy-agnostic F&O event helpers for universal backtesting.

The engine stores observations without assuming a particular strategy. This
module provides typed payload contracts for futures, options, OI and rollover
so the same replay pipeline can support single- and multi-leg strategies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class DerivativeKind(str, Enum):
    FUTURE = "future"
    OPTION = "option"


class OptionRight(str, Enum):
    CALL = "CE"
    PUT = "PE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class FNOObservation:
    instrument: str
    kind: DerivativeKind
    side: OrderSide | None = None
    option_right: OptionRight | None = None
    strike: float | None = None
    expiry: str | None = None
    price: float | None = None
    open_interest: float | None = None
    oi_change: float | None = None
    volume: float | None = None
    rollover_oi: float | None = None
    rollover_percent: float | None = None
    rollover_cost: float | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.kind == DerivativeKind.OPTION and self.option_right is None:
            raise ValueError("option_right is required for options")
        if self.kind == DerivativeKind.FUTURE and self.option_right is not None:
            raise ValueError("option_right is only valid for options")
        for name in ("price", "open_interest", "oi_change", "volume", "rollover_oi", "rollover_percent"):
            value = getattr(self, name)
            if value is not None and float(value) < 0 and name != "oi_change":
                raise ValueError(f"{name} cannot be negative")


def observation_payload(observation: FNOObservation) -> dict[str, Any]:
    """Convert an F&O observation to a generic MarketEvent payload."""
    return {
        "instrument": observation.instrument,
        "kind": observation.kind.value,
        "side": observation.side.value if observation.side else None,
        "option_right": observation.option_right.value if observation.option_right else None,
        "strike": observation.strike,
        "expiry": observation.expiry,
        "price": observation.price,
        "open_interest": observation.open_interest,
        "oi_change": observation.oi_change,
        "volume": observation.volume,
        "rollover_oi": observation.rollover_oi,
        "rollover_percent": observation.rollover_percent,
        "rollover_cost": observation.rollover_cost,
        "metadata": dict(observation.metadata or {}),
    }
