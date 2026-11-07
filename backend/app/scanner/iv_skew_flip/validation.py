"""Read-only, no-lookahead forward outcome measurements for IV signal events.

This module reports raw direction-adjusted spot returns. It deliberately does
not label a signal as accurate or profitable: thresholds, costs and a
date-grouped validation protocol must be selected separately.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from math import isfinite
from statistics import median
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
    spot_timestamp_ns: int | None = None


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
    # The entry price is the source-timestamped spot quote captured with the
    # detector event. Reject impossible/future or stale baselines rather than
    # silently treating a delayed quote as the event-time price.
    if event.spot_timestamp_ns is not None:
        if (not _valid_timestamp(event.spot_timestamp_ns)
                or event.spot_timestamp_ns > event.event_timestamp_ns
                or event.event_timestamp_ns - event.spot_timestamp_ns > 5_000_000_000):
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


def summarize_forward_outcomes(
    events: Iterable[SignalEvent],
    rows: Iterable[tuple[SignalEvent, ForwardOutcome]],
) -> dict[str, object]:
    """Return descriptive horizon coverage and returns, never an accuracy score."""
    event_rows = list(events)
    outcome_rows = list(rows)
    summaries: dict[str, object] = {}
    for horizon in (10, 30, 60):
        values = [
            outcome.direction_adjusted_return_pct
            for _, outcome in outcome_rows
            if outcome.horizon_minutes == horizon
            and isfinite(outcome.direction_adjusted_return_pct)
        ]
        summaries[str(horizon)] = {
            "observed": len(values),
            "eligible_events": len(event_rows),
            "coverage_pct": round(len(values) * 100.0 / len(event_rows), 2)
            if event_rows else None,
            "mean_direction_adjusted_return_pct": round(sum(values) / len(values), 6)
            if values else None,
            "median_direction_adjusted_return_pct": round(median(values), 6)
            if values else None,
            "positive_return_count": sum(value > 0 for value in values),
            "negative_return_count": sum(value < 0 for value in values),
            "flat_return_count": sum(value == 0 for value in values),
        }
    return {
        "eligible_events": len(event_rows),
        "measured_outcomes": len(outcome_rows),
        "horizons": summaries,
        "interpretation": "descriptive forward returns only; not accuracy or profitability",
    }


def trading_date_ist(timestamp_ns: int) -> str:
    """NSE/BSE exchange-local date key for date-grouped validation splits."""
    if not _valid_timestamp(timestamp_ns):
        raise ValueError("timestamp_ns must be a positive integer")
    return datetime.fromtimestamp(
        timestamp_ns / 1_000_000_000, tz=ZoneInfo("Asia/Kolkata")
    ).date().isoformat()


def group_outcomes_by_date(
    rows: Iterable[tuple[SignalEvent, ForwardOutcome]],
) -> dict[str, list[tuple[SignalEvent, ForwardOutcome]]]:
    """Group event/outcome pairs by event date; never split individual rows randomly."""
    grouped: dict[str, list[tuple[SignalEvent, ForwardOutcome]]] = defaultdict(list)
    for event, outcome in rows:
        grouped[trading_date_ist(event.event_timestamp_ns)].append((event, outcome))
    return dict(sorted(grouped.items()))



def load_persisted_forward_outcomes(
    session,
    *,
    underlying: str,
    expiry_type: str | None = None,
    horizons_minutes: tuple[int, ...] = (10, 30, 60),
    max_observation_delay_seconds: float = 120.0,
) -> list[tuple[SignalEvent, ForwardOutcome]]:
    """Join durable event and spot-bearing option observations, read-only.

    Reads the dedicated spot-tick stream, not option-sampler snapshots.
    The composite key makes each underlying/source timestamp unique.
    """
    from app.models.iv_signal_event import IVSignalEvent
    from app.models.iv_spot_observation import IVSpotObservation

    query = session.query(IVSignalEvent).filter(
        IVSignalEvent.underlying == underlying.upper(),
        IVSignalEvent.state.in_(("PRE_MOVE", "CONFIRMED")),
        IVSignalEvent.direction.in_(("BULLISH", "BEARISH")),
    )
    if expiry_type:
        query = query.filter(IVSignalEvent.expiry_type == expiry_type.upper())
    events = query.order_by(IVSignalEvent.event_timestamp_ns.asc()).all()
    if not events:
        return []

    try:
        delay_limit = float(max_observation_delay_seconds)
    except (TypeError, ValueError, OverflowError):
        return []
    if not isfinite(delay_limit) or delay_limit < 0:
        return []

    max_horizon = max(
        (h for h in horizons_minutes if isinstance(h, int) and not isinstance(h, bool) and h > 0),
        default=0,
    )
    if max_horizon <= 0:
        return []
    start_ns = min(event.event_timestamp_ns for event in events)
    end_ns = max(event.event_timestamp_ns for event in events) + int(
        (max_horizon * 60 + delay_limit) * 1_000_000_000
    )
    observations = session.query(
        IVSpotObservation.source_timestamp_ns,
        IVSpotObservation.price,
    ).filter(
        IVSpotObservation.underlying == underlying.upper(),
        IVSpotObservation.source_timestamp_ns > start_ns,
        IVSpotObservation.source_timestamp_ns <= end_ns,
    ).order_by(IVSpotObservation.source_timestamp_ns.asc()).all()

    spot_points = []
    for timestamp_ns, price in observations:
        if not _valid_timestamp(timestamp_ns):
            continue
        try:
            numeric_price = float(price)
        except (TypeError, ValueError, OverflowError):
            continue
        if isfinite(numeric_price) and numeric_price > 0:
            spot_points.append(SpotPoint(timestamp_ns, numeric_price))

    rows: list[tuple[SignalEvent, ForwardOutcome]] = []
    for stored in events:
        event = SignalEvent(
            event_timestamp_ns=stored.event_timestamp_ns,
            spot_price=stored.spot_price,
            direction=stored.direction,
            state=stored.state,
            underlying=stored.underlying,
            spot_timestamp_ns=stored.spot_timestamp_ns,
        )
        for outcome in measure_forward_outcomes(
            event, spot_points,
            horizons_minutes=horizons_minutes,
            max_observation_delay_seconds=max_observation_delay_seconds,
        ):
            rows.append((event, outcome))
    return rows



def summarize_walk_forward_by_date(
    rows: Iterable[tuple[SignalEvent, ForwardOutcome]],
) -> dict[str, object]:
    """Descriptive date-grouped report; each exchange date remains a whole group."""
    grouped = group_outcomes_by_date(rows)
    report = []
    for trading_date, date_rows in grouped.items():
        unique_events = {
            (event.event_timestamp_ns, event.direction, event.state, event.underlying): event
            for event, _ in date_rows
        }
        summary = summarize_forward_outcomes(unique_events.values(), date_rows)
        report.append({
            "trading_date_ist": trading_date,
            **summary,
        })
    return {
        "grouping": "event trading date in Asia/Kolkata; no random row split",
        "dates": report,
        "interpretation": "descriptive forward returns only; not accuracy, profitability, or out-of-sample validation",
    }
