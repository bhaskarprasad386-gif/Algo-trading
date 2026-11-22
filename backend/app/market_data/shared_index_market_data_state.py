"""Bounded latest-tick state and freshness metrics for shared index feed."""
from __future__ import annotations

from threading import RLock
from time import time_ns
from typing import Any, Callable
from collections import deque

from .shared_cache import put_market_data_record


class SharedIndexMarketDataState:
    MAX_INSTRUMENTS = 50  # locked universe: 48 options + 2 spot



    def __init__(self, *, max_age_seconds: float = 5.0, publish: Callable[[Any], None] | None = None):
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.max_age_ns = int(max_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._latest: dict[str, Any] = {}
        self._publish = publish or (lambda _record: None)
        self._accepted = 0
        self._stale_dropped = 0
        self._out_of_order = 0
        self._capacity_dropped = 0
        self._oi_history: dict[str, deque[tuple[int, int]]] = {}
        self._volume_history: dict[str, deque[tuple[int, int]]] = {}
        self._tick_history: dict[str, deque[tuple[int, float | None, float | None, float | None, float | None, float | None]]] = {}



    def on_record(self, record: Any) -> bool:
        key = getattr(getattr(record, "instrument", None), "value", "")
        ts = getattr(record, "timestamp_ns", None)
        if not key or isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
            return False
        now = time_ns()
        if ts > now + 1_000_000_000 or now - ts > self.max_age_ns:
            with self._lock:
                self._stale_dropped += 1
            return False
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and ts < previous.timestamp_ns:
                self._out_of_order += 1
                return False
            if key not in self._latest and len(self._latest) >= self.MAX_INSTRUMENTS:
                self._capacity_dropped += 1
                return False
            self._latest[key] = record
            cutoff = ts - 5_000_000_000
            if record.oi is not None:
                history = self._oi_history.setdefault(key, deque())
                history.append((ts, record.oi))
                while history and history[0][0] < cutoff:
                    history.popleft()
            ticks = self._tick_history.setdefault(key, deque())
            ticks.append((ts, record.ltp, record.bid, record.ask, record.bid_qty, record.ask_qty))
            while ticks and ticks[0][0] < ts - 5_000_000_000:
                ticks.popleft()
            if record.volume is not None:
                history = self._volume_history.setdefault(key, deque())
                # Broker day-volume can reset at a session boundary. Drop the
                # old baseline instead of interpreting a reset as a negative delta
                # or a later rebound as a false positive.
                if history and record.volume < history[-1][1]:
                    history.clear()
                history.append((ts, record.volume))
                while history and history[0][0] < cutoff:
                    history.popleft()
            self._accepted += 1
        put_market_data_record(record)
        self._publish(record)
        return True



    def clear(self) -> int:
        """Clear current cached records/history without resetting cumulative counters."""
        with self._lock:
            removed = len(self._latest)
            self._latest.clear()
            self._oi_history.clear()
            self._volume_history.clear()
            self._tick_history.clear()
            return removed


    def retain_instruments(self, keys) -> int:
        """Drop cached runner records for contracts no longer subscribed."""
        allowed = {key.value for key in keys}
        with self._lock:
            removed = [key for key in self._latest if key not in allowed]
            for key in removed:
                self._latest.pop(key, None)
                self._oi_history.pop(key, None)
                self._volume_history.pop(key, None)
                self._tick_history.pop(key, None)
            return len(removed)


    def records_snapshot(self) -> list[dict[str, Any]]:
        """Return bounded current records with per-contract OI change over ~1s."""
        now = time_ns()
        with self._lock:
            latest = tuple(self._latest.items())
            oi_history = {key: tuple(history) for key, history in self._oi_history.items()}
            volume_history = {key: tuple(history) for key, history in self._volume_history.items()}
            tick_history = {key: tuple(history) for key, history in self._tick_history.items()}
        result = []
        for key, record in latest:
            data = record.as_dict()
            age_ns = now - record.timestamp_ns
            data["feed_age_seconds"] = max(0.0, age_ns / 1e9)
            data["fresh"] = -1_000_000_000 <= age_ns <= self.max_age_ns
            history = oi_history.get(key, ())
            delta = None
            if record.oi is not None and len(history) >= 2:
                # Only report a true ~1s delta. Falling back to the oldest
                # sample when it is newer than 1s makes a short-window change
                # look like a one-second OI delta.
                baseline = next((oi for ts, oi in history if ts <= now - 1_000_000_000), None)
                if baseline is not None:
                    delta = record.oi - baseline
            if not data["fresh"]:
                delta = None
            data["oi_delta_1s"] = delta
            for window in (3, 5):
                baseline_oi = next((oi for stamp, oi in history if stamp <= now - window * 1_000_000_000), None)
                data[f"oi_delta_{window}s"] = (record.oi - baseline_oi) if record.oi is not None and baseline_oi is not None and data["fresh"] else None
            vol_history = volume_history.get(key, ())
            volume_delta = None
            if record.volume is not None and len(vol_history) >= 2:
                volume_baseline = next((volume for ts, volume in vol_history if ts <= now - 1_000_000_000), None)
                if volume_baseline is not None and record.volume >= volume_baseline:
                    volume_delta = record.volume - volume_baseline
            if not data["fresh"]:
                volume_delta = None
            data["volume_delta_1s"] = volume_delta
            for window in (3, 5):
                baseline_volume = next((volume for stamp, volume in vol_history if stamp <= now - window * 1_000_000_000), None)
                data[f"volume_delta_{window}s"] = (record.volume - baseline_volume) if record.volume is not None and baseline_volume is not None and record.volume >= baseline_volume and data["fresh"] else None
            ticks = tick_history.get(key, ())
            for window in (1, 3, 5):
                baseline = next((price for stamp, price, _bid, _ask, _bq, _aq in ticks if stamp <= record.timestamp_ns - window * 1_000_000_000 and price is not None), None)
                change = None if record.ltp is None or baseline is None else record.ltp - baseline
                data[f"ltp_delta_{window}s"] = change
                data[f"ltp_change_pct_{window}s"] = (change / baseline * 100.0) if change is not None and baseline else None
                data[f"ltp_velocity_{window}s"] = (change / window) if change is not None else None
            quote_valid = record.bid is not None and record.ask is not None and record.bid > 0 and record.ask > 0 and record.ask >= record.bid
            mid = ((record.bid + record.ask) / 2.0) if quote_valid else None
            spread = (record.ask - record.bid) if quote_valid else None
            data["mid_price"] = mid
            data["spread"] = spread
            data["spread_pct"] = (spread / mid * 100.0) if spread is not None and mid and mid > 0 else None
            total_qty = (record.bid_qty or 0) + (record.ask_qty or 0)
            data["quote_imbalance"] = ((record.bid_qty or 0) - (record.ask_qty or 0)) / total_qty if total_qty > 0 else None
            data["microprice"] = ((record.ask * (record.bid_qty or 0) + record.bid * (record.ask_qty or 0)) / total_qty) if quote_valid and total_qty > 0 else None
            for window in (1, 3, 5):
                quote_baseline = next((item for item in ticks if item[0] <= record.timestamp_ns - window * 1_000_000_000), None)
                for side, current_qty, index in (("bid", record.bid_qty, 4), ("ask", record.ask_qty, 5)):
                    baseline_qty = quote_baseline[index] if quote_baseline is not None else None
                    data[f"{side}_qty_delta_{window}s"] = current_qty - baseline_qty if current_qty is not None and baseline_qty is not None and data["fresh"] else None
                baseline_spread = (quote_baseline[3] - quote_baseline[2]) if quote_baseline is not None and quote_baseline[2] is not None and quote_baseline[3] is not None else None
                data[f"spread_delta_{window}s"] = spread - baseline_spread if spread is not None and baseline_spread is not None and data["fresh"] else None
            for window in (1, 3, 5):\n                data[f"tick_rate_{window}s"] = sum(1 for stamp, *_ in ticks if stamp >= record.timestamp_ns - window * 1_000_000_000)
            if not data["fresh"]:
                for window in (1, 3, 5):
                    data[f"ltp_delta_{window}s"] = None
                    data[f"ltp_change_pct_{window}s"] = None
                    data[f"ltp_velocity_{window}s"] = None
                for metric in ("mid_price", "spread", "spread_pct", "quote_imbalance", "microprice", "tick_rate_1s", "tick_rate_3s", "tick_rate_5s"):
                    data[metric] = None
                for window in (1, 3, 5):
                    for metric in (f"bid_qty_delta_{window}s", f"ask_qty_delta_{window}s", f"spread_delta_{window}s"):
                        data[metric] = None
            result.append(data)
        return result


    def latest(self, key: str) -> Any | None:
        with self._lock:
            record = self._latest.get(str(key))
        if record is None:
            return None
        age_ns = time_ns() - record.timestamp_ns
        if age_ns > self.max_age_ns or age_ns < -1_000_000_000:
            return None
        return record



    def snapshot(self) -> dict[str, Any]:
        now = time_ns()
        with self._lock:
            latest = tuple(self._latest.items())
            accepted = self._accepted
            stale = self._stale_dropped
            out_of_order = self._out_of_order
            capacity_dropped = self._capacity_dropped
        ages = {key: max(0, (now - record.timestamp_ns) / 1e9) for key, record in latest}
        fresh = sum(age <= self.max_age_ns / 1e9 for age in ages.values())
        return {
            "instrument_count": len(latest),
            "fresh_count": fresh,
            "stale_count": len(ages) - fresh,
            "feed_fresh": bool(ages) and fresh == len(ages),
            "max_age_seconds": self.max_age_ns / 1e9,
            "max_feed_age_seconds": max(ages.values(), default=None),
            "accepted_ticks": accepted,
            "stale_ticks_dropped": stale,
            "out_of_order_ticks_dropped": out_of_order,
            "capacity_ticks_dropped": capacity_dropped,
        }
