"""IV-owned live index spot/futures basis analysis.

This module is intentionally calculation/selection-only: it does not start a
broker socket, place orders, or mutate any other strategy's subscriptions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from threading import RLock
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class IndexFutureContract:
    underlying: str
    symbol: str
    token: str
    exchange: str
    expiry: date


@dataclass(frozen=True)
class BasisObservation:
    timestamp_ns: int
    spot: float
    future: float
    basis_points: float
    basis_pct: float


def _expiry(value: Any) -> date | None:
    raw = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def resolve_index_future_contracts(
    instruments: Iterable[Mapping[str, Any]],
    *,
    underlying: str,
    today: date,
    limit: int = 2,
) -> tuple[IndexFutureContract, ...]:
    """Select nearest distinct NFO FUTIDX expiries for one supported index."""
    symbol = str(underlying or "").strip().upper()
    if symbol not in {"NIFTY", "BANKNIFTY"}:
        raise ValueError(f"unsupported IV basis underlying: {symbol}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    by_expiry: dict[date, IndexFutureContract] = {}
    for row in instruments:
        if str(row.get("exch_seg", "")).strip().upper() != "NFO":
            continue
        if str(row.get("instrumenttype", "")).strip().upper() != "FUTIDX":
            continue
        if str(row.get("name", "")).strip().upper() != symbol:
            continue
        expiry = _expiry(row.get("expiry"))
        token = str(row.get("token") or "").strip()
        trading_symbol = str(row.get("symbol") or "").strip()
        if expiry is None or expiry < today or not token.isdigit() or int(token) <= 0 or not trading_symbol:
            continue
        contract = IndexFutureContract(symbol, trading_symbol, token, "NFO", expiry)
        previous = by_expiry.get(expiry)
        if previous is not None and previous.token != token:
            raise LookupError(f"duplicate {symbol} FUTIDX contracts for expiry {expiry.isoformat()}")
        by_expiry[expiry] = contract
    if not by_expiry:
        raise LookupError(f"no valid NFO FUTIDX contracts found for {symbol}")
    return tuple(by_expiry[expiry] for expiry in sorted(by_expiry)[:limit])


def _positive_price(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive price")
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite positive price") from None
    if not isfinite(price) or price <= 0:
        raise ValueError(f"{name} must be a finite positive price")
    return price


def calculate_basis(
    *,
    spot: Any,
    future: Any,
    spot_timestamp_ns: int,
    future_timestamp_ns: int,
    now_ns: int,
    max_age_ns: int = 3_000_000_000,
    max_pair_skew_ns: int = 2_000_000_000,
) -> dict[str, Any]:
    """Calculate a paired basis only when both provider timestamps are fresh."""
    spot_price = _positive_price(spot, "spot")
    future_price = _positive_price(future, "future")
    for name, value in (("spot_timestamp_ns", spot_timestamp_ns), ("future_timestamp_ns", future_timestamp_ns), ("now_ns", now_ns)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if max_age_ns <= 0 or max_pair_skew_ns < 0:
        raise ValueError("freshness limits are invalid")
    spot_age = now_ns - spot_timestamp_ns
    future_age = now_ns - future_timestamp_ns
    skew = abs(spot_timestamp_ns - future_timestamp_ns)

    # Exchange timestamps can differ from the local monotonic clock by a few
    # hundred nanoseconds during paired-feed delivery. Tolerate at most 1 ms
    # of clock lead, but never use that tolerance for a materially future tick.
    clock_jitter_tolerance_ns = 1_000_000
    effective_spot_age = max(0, spot_age) if -clock_jitter_tolerance_ns <= spot_age < 0 else spot_age
    effective_future_age = max(0, future_age) if -clock_jitter_tolerance_ns <= future_age < 0 else future_age
    fresh = (
        0 <= effective_spot_age <= max_age_ns
        and 0 <= effective_future_age <= max_age_ns
        and skew <= max_pair_skew_ns
    )
    if fresh:
        status = "LIVE"
    elif skew > max_pair_skew_ns or (spot_age >= 0 and future_age >= 0):
        status = "STALE"
    else:
        status = "INVALID_TIMESTAMP"
    basis = future_price - spot_price
    return {
        "status": status,
        "spot": spot_price,
        "future": future_price,
        "basis_points": round(basis, 4),
        "basis_pct": round((basis / spot_price) * 100.0, 6),
        "spot_timestamp_ns": spot_timestamp_ns,
        "future_timestamp_ns": future_timestamp_ns,
        "spot_age_ms": round(spot_age / 1_000_000, 2),
        "future_age_ms": round(future_age / 1_000_000, 2),
        "pair_skew_ms": round(skew / 1_000_000, 2),
        "fresh": fresh,
        "signals_allowed": fresh,
    }


class BasisHistory:
    """Bounded paired-basis history with 1/5/15-minute change summaries."""

    WINDOWS_NS = {"1m": 60_000_000_000, "5m": 300_000_000_000, "15m": 900_000_000_000}

    def __init__(self, *, max_points: int = 1800) -> None:
        if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 2:
            raise ValueError("max_points must be at least 2")
        self._items: deque[BasisObservation] = deque(maxlen=max_points)
        self._lock = RLock()

    def add(self, snapshot: Mapping[str, Any]) -> bool:
        if snapshot.get("fresh") is not True:
            return False
        stamp = snapshot.get("timestamp_ns")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= 0:
            return False
        observation = BasisObservation(
            timestamp_ns=stamp,
            spot=_positive_price(snapshot.get("spot"), "spot"),
            future=_positive_price(snapshot.get("future"), "future"),
            basis_points=float(snapshot["basis_points"]),
            basis_pct=float(snapshot["basis_pct"]),
        )
        if not isfinite(observation.basis_points) or not isfinite(observation.basis_pct):
            return False
        with self._lock:
            if self._items and stamp <= self._items[-1].timestamp_ns:
                return False
            self._items.append(observation)
        return True

    def snapshot(self, now_ns: int) -> dict[str, Any]:
        with self._lock:
            items = tuple(self._items)
        latest = items[-1] if items else None
        windows: dict[str, Any] = {}
        for label, width in self.WINDOWS_NS.items():
            reference = next((item for item in reversed(items[:-1]) if latest and item.timestamp_ns <= latest.timestamp_ns - width), None)
            windows[label] = None if latest is None or reference is None else {
                "basis_change_points": round(latest.basis_points - reference.basis_points, 4),
                "basis_change_pct": round(latest.basis_pct - reference.basis_pct, 6),
                "reference_timestamp_ns": reference.timestamp_ns,
            }
        if latest is None:
            trend = "WAITING"
        elif windows["1m"] is None:
            trend = "WARMING"
        else:
            change = windows["1m"]["basis_change_points"]
            trend = "WIDENING" if change > 0 else "NARROWING" if change < 0 else "FLAT"
        return {
            "status": "WARMING" if latest is None else "READY",
            "trend": trend,
            "latest": None if latest is None else {
                "timestamp_ns": latest.timestamp_ns,
                "spot": latest.spot,
                "future": latest.future,
                "basis_points": latest.basis_points,
                "basis_pct": latest.basis_pct,
            },
            "windows": windows,
            "sample_count": len(items),
            "signals_allowed": latest is not None and now_ns >= latest.timestamp_ns,
        }
