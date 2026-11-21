"""Timestamp-aligned immutable market snapshot builder for Early Impulse Pro.

Feed integration remains opt-in; this helper rejects unsynchronized inputs rather
than letting five engines score observations from different moments.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TimedQuote:
    instrument: str
    timestamp_ns: int
    price: float
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    volume_delta: float | None = None


def _valid_quote(item: Any) -> bool:
    return (
        isinstance(item, TimedQuote)
        and isinstance(item.instrument, str) and bool(item.instrument.strip())
        and isinstance(item.timestamp_ns, int) and not isinstance(item.timestamp_ns, bool) and item.timestamp_ns > 0
        and isinstance(item.price, (int, float)) and not isinstance(item.price, bool) and isfinite(item.price) and item.price > 0
    )


def build_synchronized_snapshot(
    observations: Mapping[str, Sequence[TimedQuote]],
    *,
    now_ns: int,
    max_age_ns: int = 2_000_000_000,
    max_skew_ns: int = 500_000_000,
    required_instruments: Sequence[str] = (),
) -> dict[str, Any]:
    """Choose latest valid observation per instrument and fail closed on skew/age."""
    if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0:
        raise ValueError("now_ns must be a positive integer")
    if max_age_ns <= 0 or max_skew_ns < 0:
        raise ValueError("age/skew limits invalid")
    required = tuple(dict.fromkeys(str(x).strip() for x in required_instruments if str(x).strip()))
    latest: dict[str, TimedQuote] = {}
    invalid_count = 0
    for name, series in observations.items():
        key = str(name).strip()
        if not key:
            invalid_count += len(series)
            continue
        valid = [q for q in series if _valid_quote(q) and q.instrument.strip() == key]
        invalid_count += len(series) - len(valid)
        if valid:
            latest[key] = max(valid, key=lambda q: q.timestamp_ns)
    missing = [name for name in required if name not in latest]
    timestamps = [q.timestamp_ns for q in latest.values() if q.timestamp_ns <= now_ns]
    future = [name for name, q in latest.items() if q.timestamp_ns > now_ns]
    age_by = {name: now_ns - q.timestamp_ns for name, q in latest.items() if q.timestamp_ns <= now_ns}
    stale = [name for name, age in age_by.items() if age > max_age_ns]
    if timestamps:
        skew = max(timestamps) - min(timestamps)
    else:
        skew = None
    aligned = bool(latest) and not missing and not future and not stale and skew is not None and skew <= max_skew_ns
    quotes = {
        name: {
            "timestamp_ns": q.timestamp_ns, "price": q.price, "bid": q.bid, "ask": q.ask,
            "bid_qty": q.bid_qty, "ask_qty": q.ask_qty, "volume_delta": q.volume_delta,
            "age_ns": age_by.get(name),
        }
        for name, q in latest.items()
    }
    return {
        "aligned": aligned, "now_ns": now_ns, "max_age_ns": max_age_ns,
        "max_skew_ns": max_skew_ns, "observed_skew_ns": skew,
        "missing_instruments": missing, "future_timestamp_instruments": future,
        "stale_instruments": stale, "invalid_observation_count": invalid_count,
        "quotes": quotes, "scanner_only": True, "order_routing_enabled": False,
    }
