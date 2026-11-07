"""Read-only, no-lookahead forward outcome measurements for IV signal events.

This module reports raw direction-adjusted spot returns. It deliberately does
not label a signal as accurate or profitable: thresholds, costs and a
date-grouped validation protocol must be selected separately.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class SpotPoint:
    timestamp_ns: int
    price: float


@dataclass(frozen=True)
class SignalEvent:
    event_timestamp_ns: int
    spot_price: float
    direction: str
    state: str
    underlying: str = ""


@dataclass(frozen=True)
class ForwardOutcome:
    horizon_minutes: int
    target_timestamp_ns: int
    observed_timestamp_ns: int
    observed_price: float
    raw_return_pct: float
    direction_adjusted_return_pct: float
    observation_delay_seconds: float


def _valid_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def measure_forward_outcomes(
    event: SignalEvent,
    spot_points: Iterable[SpotPoint],
    *,
    horizons_minutes: tuple[int, ...] = (10, 30, 60),
    max_observation_delay_seconds: float = 120.0,
) -> list[ForwardOutcome]:
    """Measure the first spot observation at/after each horizon target.

    No observation before the target is accepted. Missing/late observations
    are omitted rather than interpolated or forward-filled.
    """
    direction = str(event.direction).upper()
    if direction not in {"BULLISH", "BEARISH"}:
        return []
    if str(event.state).upper() not in {"PRE_MOVE", "CONFIRMED"}:
        return []
    if not _valid_timestamp(event.event_timestamp_ns):
        return []
    try:
        entry = float(event.spot_price)
        delay_limit = float(max_observation_delay_seconds)
    except (TypeError, ValueError, OverflowError):
        return []
    if not isfinite(entry) or entry <= 0 or not isfinite(delay_limit) or delay_limit < 0:
        return []

    valid_points = []
    for point in spot_points:
        if not _valid_timestamp(point.timestamp_ns) or point.timestamp_ns <= event.event_timestamp_ns:
            continue
        try:
            price = float(point.price)
        except (TypeError, ValueError, OverflowError):
            continue
        if isfinite(price) and price > 0:
            valid_points.append((point.timestamp_ns, price))
    valid_points.sort(key=lambda item: item[0])
    if not valid_points:
        return []

    outcomes = []
    seen_horizons = set()
    for horizon in horizons_minutes:
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0 or horizon in seen_horizons:
            continue
        seen_horizons.add(horizon)
        target_ns = event.event_timestamp_ns + horizon * 60 * 1_000_000_000
        selected = next((row for row in valid_points if row[0] >= target_ns), None)
        if selected is None:
            continue
        observed_ns, observed_price = selected
        delay_seconds = (observed_ns - target_ns) / 1_000_000_000
        if delay_seconds > delay_limit:
            continue
        raw_return = (observed_price - entry) * 100.0 / entry
        adjusted = raw_return if direction == "BULLISH" else -raw_return
        outcomes.append(ForwardOutcome(
            horizon_minutes=horizon,
            target_timestamp_ns=target_ns,
            observed_timestamp_ns=observed_ns,
            observed_price=observed_price,
            raw_return_pct=round(raw_return, 6),
            direction_adjusted_return_pct=round(adjusted, 6),
            observation_delay_seconds=round(delay_seconds, 3),
        ))
    return outcomes


def trading_date_utc(timestamp_ns: int) -> str:
    """Stable UTC date key for date-grouped splits; convert to exchange TZ upstream if needed."""
    if not _valid_timestamp(timestamp_ns):
        raise ValueError("timestamp_ns must be a positive integer")
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()


def group_outcomes_by_date(
    rows: Iterable[tuple[SignalEvent, ForwardOutcome]],
) -> dict[str, list[tuple[SignalEvent, ForwardOutcome]]]:
    """Group event/outcome pairs by event date; never split individual rows randomly."""
    grouped: dict[str, list[tuple[SignalEvent, ForwardOutcome]]] = defaultdict(list)
    for event, outcome in rows:
        grouped[trading_date_utc(event.event_timestamp_ns)].append((event, outcome))
    return dict(sorted(grouped.items()))
