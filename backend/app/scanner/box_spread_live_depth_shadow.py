"""Isolated, in-memory Box Spread market-depth shadow consumer.

This adapter is intentionally opt-in and does not feed the live scanner or
paper execution. It registers its own consumer on the existing shared
WebSocket manager; stopping it clears only that consumer.
"""
from __future__ import annotations

from collections import OrderedDict
from math import isfinite
from threading import RLock
from time import monotonic, time_ns
from typing import Any, Iterable, Mapping

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.registry import InstrumentDescriptor


_CONSUMER = "box-live-depth-shadow"
_SIDES = (("BID", "buy"), ("ASK", "sell"))


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def _raw_levels(payload: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    """Read broker depth only; deliberately do not fall back to canonical L1."""
    levels = None
    for key in (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            levels = candidate
            break
        if isinstance(candidate, Mapping):
            levels = [candidate]
            break
    parsed: list[tuple[float, float]] = []
    for level in levels or ():
        if not isinstance(level, Mapping):
            continue
        price = _positive_number(level.get("price") or level.get(f"{side}_price"))
        quantity = _positive_number(
            level.get("quantity") or level.get("qty") or level.get(f"{side}_quantity")
        )
        if price is not None and quantity is not None:
            parsed.append((price / 100.0, quantity))
    parsed.sort(key=lambda item: item[0], reverse=(side == "buy"))
    return parsed[:5]


class BoxLiveDepthShadowFeed:
    """Bounded shadow-only depth cache backed by the shared common feed."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: int = 3,
        max_records: int = 512,
        max_arrival_age_seconds: float = 5.0,
        max_provider_age_seconds: float = 5.0,
        feed: Any | None = None,
    ) -> None:
        if isinstance(max_records, bool) or int(max_records) < 1:
            raise ValueError("max_records must be a positive integer")
        self.enabled = bool(enabled)
        self.max_records = int(max_records)
        self.max_arrival_age_seconds = float(max_arrival_age_seconds)
        self.max_provider_age_seconds = float(max_provider_age_seconds)
        self._feed = feed or CommonStrategyMarketFeed(_CONSUMER, mode=mode)
        self._lock = RLock()
        self._records: OrderedDict[Any, dict[str, Any]] = OrderedDict()
        self._started = False
        self._counts = {"received": 0, "depth_full": 0, "depth_partial": 0, "l1_only": 0, "invalid_timestamp": 0}

    def start(self, descriptors: Iterable[InstrumentDescriptor]) -> bool:
        """Subscribe only when explicitly enabled; otherwise make no feed changes."""
        if not self.enabled:
            return False
        items = tuple(descriptors)
        if not items:
            raise ValueError("at least one Box Spread option descriptor is required")
        if any(str(item.instrument_type).strip().lower() != "option" for item in items):
            raise ValueError("Box Live Depth Shadow accepts option descriptors only")
        with self._lock:
            if self._started:
                return True
            self._records.clear()
        self._feed.start(items, self._on_record)
        with self._lock:
            self._started = True
        return True

    def _on_record(self, record: Any) -> None:
        instrument = getattr(record, "instrument", None)
        payload = getattr(record, "payload", None)
        if instrument is None or not isinstance(payload, Mapping):
            return
        timestamp_ns = getattr(record, "timestamp_ns", None)
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
            with self._lock:
                self._counts["invalid_timestamp"] += 1
            return

        bids = _raw_levels(payload, "buy")
        asks = _raw_levels(payload, "sell")
        if not bids and not asks:
            state = "L1_ONLY"
        elif len(bids) >= 5 and len(asks) >= 5:
            state = "DEPTH_FULL"
        else:
            state = "DEPTH_PARTIAL"

        # Crossed/invalid best levels are not eligible for shadow comparison.
        if bids and asks and bids[0][0] > asks[0][0]:
            state = "DEPTH_INVALID"

        entry = {
            "instrument": instrument.value,
            "symbol": str(getattr(record, "symbol", "")),
            "timestamp_ns": timestamp_ns,
            "arrival_monotonic": monotonic(),
            "depth_status": state,
            "bids": bids,
            "asks": asks,
        }
        with self._lock:
            self._counts["received"] += 1
            if state in self._counts:
                self._counts[state.lower()] += 1
            self._records[instrument] = entry
            self._records.move_to_end(instrument)
            while len(self._records) > self.max_records:
                self._records.popitem(last=False)

    def snapshot(self, *, now_ns: int | None = None, now_monotonic: float | None = None) -> dict[str, Any]:
        """Return fresh in-memory observations; never persists or changes scanner data."""
        wall_now = time_ns() if now_ns is None else int(now_ns)
        arrival_now = monotonic() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            rows = []
            for item in self._records.values():
                provider_age = (wall_now - item["timestamp_ns"]) / 1e9
                arrival_age = arrival_now - item["arrival_monotonic"]
                fresh = (
                    -1.0 <= provider_age <= self.max_provider_age_seconds
                    and 0.0 <= arrival_age <= self.max_arrival_age_seconds
                )
                rows.append({
                    "instrument": item["instrument"],
                    "symbol": item["symbol"],
                    "timestamp_ns": item["timestamp_ns"],
                    "provider_age_seconds": round(provider_age, 3),
                    "arrival_age_seconds": round(arrival_age, 3),
                    "freshness": "FRESH" if fresh else "STALE",
                    "depth_status": item["depth_status"],
                    "bids": list(item["bids"]),
                    "asks": list(item["asks"]),
                })
            return {
                "enabled": self.enabled,
                "running": self._started,
                "consumer": _CONSUMER,
                "record_count": len(rows),
                "counts": dict(self._counts),
                "records": rows,
            }

    def stop(self) -> None:
        """Detach only this Box Spread consumer; never stop the shared socket."""
        with self._lock:
            started = self._started
            self._started = False
        if started:
            self._feed.stop()
        with self._lock:
            self._records.clear()

    def close(self) -> None:
        self.stop()


__all__ = ["BoxLiveDepthShadowFeed"]
