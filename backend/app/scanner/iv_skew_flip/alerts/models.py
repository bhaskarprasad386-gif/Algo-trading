"""Typed, expiry-scoped inputs and outputs for early-entry IV alerts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AlertState(StrEnum):
    WATCH = "WATCH"
    EARLY_ENTRY = "EARLY_ENTRY"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class OptionObservation:
    """One real broker observation. All timestamps are Unix nanoseconds."""
    token: str
    underlying: str
    expiry: str
    strike: float
    option_type: str
    source_timestamp_ns: int
    sampled_at_ns: int
    spot_timestamp_ns: int
    spot_price: float
    iv: float | None
    iv_input: str | None
    bid: float | None
    ask: float | None
    ltp: float | None
    oi: int | None
    oi_timestamp_ns: int | None
    volume: int | None


@dataclass(frozen=True, slots=True)
class SpotBar:
    """Completed one-minute spot bucket; timestamps are Unix nanoseconds."""
    timestamp_ns: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    strategy: str
    underlying: str
    expiry: str
    strike: float
    direction: str
    option_type: str
    state: AlertState
    observed_at_ns: int
    spot: float
    premium: float | None
    iv_change_points: float | None
    volume_ratio: float | None
    reason: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    state: AlertState
    candidate: AlertCandidate | None = None
    reasons: tuple[str, ...] = ()
