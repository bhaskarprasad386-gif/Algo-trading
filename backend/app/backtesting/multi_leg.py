"""Generic atomic multi-leg strategy contracts for universal backtests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class LegSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class StrategyLeg:
    """One deterministic leg of a strategy basket."""

    leg_id: str
    instrument: str
    side: LegSide
    quantity: int

    def __post_init__(self) -> None:
        if not self.leg_id.strip() or not self.instrument.strip():
            raise ValueError("leg_id and instrument are required")
        if self.quantity <= 0:
            raise ValueError("leg quantity must be greater than zero")


@dataclass(frozen=True)
class MultiLegSignal:
    """Atomic basket signal: all legs are intended to participate together."""

    signal_id: str
    timestamp_ns: int
    legs: tuple[StrategyLeg, ...]
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not self.legs:
            raise ValueError("at least one leg is required")
        ids = [leg.leg_id for leg in self.legs]
        if len(ids) != len(set(ids)):
            raise ValueError("leg_id values must be unique")
        # Stable ordering makes replay and persisted results deterministic.
        if tuple(ids) != tuple(sorted(ids)):
            raise ValueError("legs must be supplied in deterministic leg_id order")


class MultiLegExecutor:
    """Validate basket completeness before delegating fills to a shared executor."""

    @staticmethod
    def validate_market(snapshot: Mapping[str, float], signal: MultiLegSignal) -> None:
        """Fail closed when any required leg lacks a valid point-in-time price."""
        missing = [leg.leg_id for leg in signal.legs if leg.instrument not in snapshot]
        if missing:
            raise LookupError(f"missing market data for legs: {', '.join(missing)}")
        invalid = [leg.leg_id for leg in signal.legs if float(snapshot[leg.instrument]) <= 0]
        if invalid:
            raise ValueError(f"non-positive market price for legs: {', '.join(invalid)}")

    @staticmethod
    def ordered_legs(signal: MultiLegSignal) -> tuple[StrategyLeg, ...]:
        return tuple(sorted(signal.legs, key=lambda leg: leg.leg_id))
