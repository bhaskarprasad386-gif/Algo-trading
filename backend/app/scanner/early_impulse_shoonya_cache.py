"""Isolated Shoonya quote cache for Early Impulse Pro (scanner-only).

This module is deliberately not wired into the existing scanner consumers. It is a
bounded, fail-closed cache that can be attached as a separate read-only observer.
"""
from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from time import monotonic_ns
from typing import Any

from app.market_data.contracts import MarketDataRecord


class EarlyImpulseShoonyaCache:
    def __init__(self, *, capacity: int = 500, max_age_seconds: float = 5.0) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.capacity = capacity
        self.max_age_ns = int(max_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._records: OrderedDict[tuple[str, str, str], tuple[MarketDataRecord, int, int]] = OrderedDict()
        self._last_exchange_ts: dict[tuple[str, str, str], int] = {}
        self._feed_state = "DISCONNECTED"
        self._feed_reason: str | None = None
        self._feed_epoch: int | None = None
        self._received_count = 0
        self._rejected_count = 0
        self._last_received_ns: int | None = None
        self._session_authenticated = False
        self._last_session_observation_ns = 0

    @staticmethod
    def _key(record: MarketDataRecord) -> tuple[str, str, str]:
        key = record.instrument
        return (key.exchange.strip().upper(), key.segment.strip().upper(), key.token.strip())

    def on_tick_observation(self, record: MarketDataRecord, feed_epoch: int, authenticated: bool, receive_monotonic_ns: int) -> bool:
        if not isinstance(record, MarketDataRecord) or not authenticated:
            with self._lock:
                self._rejected_count += 1
            return False
        if isinstance(receive_monotonic_ns, bool) or not isinstance(receive_monotonic_ns, int) or receive_monotonic_ns <= 0:
            with self._lock:
                self._rejected_count += 1
            return False
        if record.timestamp_ns <= 0 or not record.is_executable_quote:
            with self._lock:
                self._rejected_count += 1
            return False
        key = self._key(record)
        with self._lock:
            # A tick is accepted only for the current authenticated session epoch.
            # Session lifecycle callbacks are the authoritative epoch boundary.
            if feed_epoch != self._feed_epoch or not self._session_authenticated or self._feed_state not in {"LIVE", "CONNECTED_NO_TICKS"}:
                self._rejected_count += 1
                return False
            previous = self._last_exchange_ts.get(key)
            if previous is not None and record.timestamp_ns <= previous:
                self._rejected_count += 1
                return False
            self._last_exchange_ts[key] = record.timestamp_ns
            self._records[key] = (record, int(feed_epoch), receive_monotonic_ns)
            self._records.move_to_end(key)
            while len(self._records) > self.capacity:
                evicted, _ = self._records.popitem(last=False)
                self._last_exchange_ts.pop(evicted, None)
            self._feed_state = "LIVE"
            self._feed_reason = None
            self._received_count += 1
            self._last_received_ns = receive_monotonic_ns
        return True

    def on_session_observation(self, state: str, reason: str | None, feed_epoch: int, authenticated: bool, connected: bool, observed_ns: int) -> None:
        state_value = str(state).strip().upper() or "UNKNOWN"
        if (isinstance(feed_epoch, bool) or not isinstance(feed_epoch, int) or feed_epoch < 0
                or isinstance(observed_ns, bool) or not isinstance(observed_ns, int) or observed_ns <= 0):
            return
        with self._lock:
            if feed_epoch < (self._feed_epoch if self._feed_epoch is not None else 0):
                return
            if feed_epoch == self._feed_epoch and observed_ns <= self._last_session_observation_ns:
                return
            self._last_session_observation_ns = observed_ns
            epoch_changed = self._feed_epoch != int(feed_epoch)
            if epoch_changed or not connected:
                # Clear old-generation data and exchange timestamp monotonicity state.
                # Prevent reconnect from being blocked by pre-reconnect timestamps.
                self._records.clear()
                self._last_exchange_ts.clear()
                self._last_received_ns = None
            self._feed_epoch = int(feed_epoch)
            self._session_authenticated = bool(connected and authenticated)
            self._feed_reason = (str(reason)[:240] if reason else None)
            if connected and authenticated:
                self._feed_state = "LIVE" if self._last_received_ns is not None else "CONNECTED_NO_TICKS"
            elif connected:
                self._feed_state = "AUTH_PENDING"
            else:
                self._feed_state = "DISCONNECTED"
            if not connected:
                # Retain last quotes for diagnostics only; consumers get no fresh data.
                self._feed_reason = self._feed_reason or state_value

    def snapshot(self, *, now_monotonic_ns: int | None = None) -> dict[str, Any]:
        now = monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        with self._lock:
            fresh = []
            stale = 0
            for record, epoch, received_ns in self._records.values():
                age = now - received_ns
                if 0 <= age <= self.max_age_ns and epoch == self._feed_epoch and self._feed_state == "LIVE":
                    fresh.append(record.as_dict())
                else:
                    stale += 1
            return {
                "provider": "SHOONYA",
                "status": self._feed_state,
                "connected": self._feed_state in {"LIVE", "CONNECTED_NO_TICKS", "AUTH_PENDING"},
                "fresh_count": len(fresh),
                "stale_count": stale,
                "received_count": self._received_count,
                "rejected_count": self._rejected_count,
                "max_age_seconds": self.max_age_ns / 1_000_000_000,
                "feed_epoch": self._feed_epoch,
                "last_received_ns": self._last_received_ns,
                "reason": self._feed_reason,
                "quotes": fresh,
                "signal_generation_enabled": False,
                "order_routing_enabled": False,
            }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._last_exchange_ts.clear()
            self._feed_state = "DISCONNECTED"
            self._feed_reason = "CACHE_CLEARED"
            self._feed_epoch = None
            self._last_received_ns = None
