"""Generic F&O market-event models for OI, rollover, futures and options.

The models are data contracts only: they never invent missing observations and
can be consumed by candle, tick, quote or depth replay strategies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class FnoInstrumentType(str, Enum):
    FUTURE = "future"
    OPTION = "option"


class OptionSide(str, Enum):
    CE = "CE"
    PE = "PE"


class PositionAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class OpenInterestSnapshot:
    timestamp_ns: int
    instrument: str
    oi: int
    oi_change: int = 0
    price: float | None = None
    volume: int | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.oi < 0:
            raise ValueError("oi must be non-negative")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be non-negative")


@dataclass(frozen=True)
class RolloverSnapshot:
    timestamp_ns: int
    underlying: str
    expiry_from: str
    expiry_to: str
    near_oi: int
    next_oi: int
    rollover_oi: int
    rollover_percent: float | None = None
    rollover_cost_percent: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if not self.underlying or not self.expiry_from or not self.expiry_to:
            raise ValueError("underlying and expiry fields are required")
        for name, value in (("near_oi", self.near_oi), ("next_oi", self.next_oi), ("rollover_oi", self.rollover_oi)):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.rollover_percent is not None and self.rollover_percent < 0:
            raise ValueError("rollover_percent must be non-negative")


@dataclass(frozen=True)
class FnoContract:
    instrument: str
    instrument_type: FnoInstrumentType
    underlying: str
    expiry: str
    lot_size: int
    strike: float | None = None
    option_side: OptionSide | None = None

    def __post_init__(self) -> None:
        if not self.instrument or not self.underlying or not self.expiry:
            raise ValueError("instrument, underlying and expiry are required")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if self.instrument_type == FnoInstrumentType.OPTION:
            if self.strike is None or self.option_side is None:
                raise ValueError("option contracts require strike and option_side")
        elif self.strike is not None or self.option_side is not None:
            raise ValueError("future contracts cannot define option fields")


@dataclass(frozen=True)
class FnoMarketContext:
    """Point-in-time inputs a strategy may consume without coupling to a feed."""

    values: Mapping[str, float]
    oi: Mapping[str, OpenInterestSnapshot] | None = None
    rollover: Mapping[str, RolloverSnapshot] | None = None


@dataclass(frozen=True)
class StrategyLeg:
    instrument: str
    action: PositionAction
    quantity: int
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price is not None and self.limit_price < 0:
            raise ValueError("limit_price must be non-negative")
