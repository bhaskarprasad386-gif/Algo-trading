"""Shared Shoonya observer fan-out for Early Impulse Pro.

Callbacks are additive and isolated: readiness diagnostics keep their existing
consumer, while this scanner receives immutable market records through the same
supervisor observer contract. Exceptions are swallowed at this boundary so the
broker feed callback cannot be disrupted.
"""
from __future__ import annotations

from threading import RLock
from typing import Any

from app.market_data.provider_readiness_bridge import ProviderReadinessBridge
from app.scanner.early_impulse_shoonya_cache import EarlyImpulseShoonyaCache


class EarlyImpulseShoonyaObserver:
    def __init__(self, *, readiness: ProviderReadinessBridge | None = None,
                 cache: EarlyImpulseShoonyaCache | None = None) -> None:
        self.readiness = readiness or ProviderReadinessBridge()
        self.cache = cache or EarlyImpulseShoonyaCache()
        self._lock = RLock()
        self._observer_errors = 0

    def on_session_observation(self, state: str, reason: str | None, epoch: int,
                              authenticated: bool, connected: bool, observed_ns: int) -> None:
        # Fan out to both existing readiness evidence and the strategy cache.
        try:
            self.readiness.shoonya_state(state, reason, epoch, connected, authenticated, observed_ns)
        except Exception:
            with self._lock:
                self._observer_errors += 1
        try:
            self.cache.on_session_observation(state, reason, epoch, authenticated, connected, observed_ns)
        except Exception:
            with self._lock:
                self._observer_errors += 1

    def on_tick_observation(self, record: Any, epoch: int, authenticated: bool,
                           received_monotonic_ns: int) -> None:
        try:
            self.readiness.shoonya_market_event(record, epoch, authenticated, received_monotonic_ns)
        except Exception:
            with self._lock:
                self._observer_errors += 1
        try:
            self.cache.on_tick_observation(record, epoch, authenticated, received_monotonic_ns)
        except Exception:
            with self._lock:
                self._observer_errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            errors = self._observer_errors
        return {**self.cache.snapshot(), "observer_errors": errors,
                "scanner_enabled": False, "signal_generation_enabled": False,
                "order_routing_enabled": False}


early_impulse_observer = EarlyImpulseShoonyaObserver()
