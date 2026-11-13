"""Default-off Shoonya runtime owner with an isolated instrument registry.

This supervisor deliberately does not reuse the Angel One shared feed or route
records into trading/scanner consumers. It is a shadow diagnostics boundary
until a separately reviewed provider-selection integration is approved.
"""
from __future__ import annotations

from threading import RLock
from time import time
from typing import Any, Iterable

from app.core.logger import app_logger
from .registry import InstrumentDescriptor, InstrumentRegistry
from .shoonya_websocket import ShoonyaWebSocket


class ShoonyaMarketDataSupervisor:
    """Own one Shoonya adapter and its broker-specific subscription registry."""

    def __init__(
        self,
        *,
        uid: str,
        actid: str,
        access_token: str,
        max_tokens: int = 500,
        max_dispatch_queue: int = 10000,
        reconnect_min_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        websocket_factory: Any | None = None,
    ) -> None:
        if not 1 <= int(max_tokens) <= 500:
            raise ValueError("max_tokens must be between 1 and 500")
        self.registry = InstrumentRegistry()
        self._lock = RLock()
        self._enabled = True
        self._max_tokens = int(max_tokens)
        self._descriptors: dict[Any, InstrumentDescriptor] = {}
        self._last_tick_at: float | None = None
        self._ticks_observed = 0
        self._order_events_observed = 0
        self._last_state = "IDLE_NO_SUBSCRIPTIONS"
        self._last_state_reason: str | None = None
        self._adapter = ShoonyaWebSocket(
            uid=uid,
            actid=actid,
            access_token=access_token,
            on_tick=self._observe_tick,
            on_order_update=self._observe_order_update,
            on_state=self._observe_state,
            websocket_factory=websocket_factory,
            max_dispatch_queue=max_dispatch_queue,
            reconnect_min_seconds=reconnect_min_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
        )

    def register(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        with self._lock:
            if not self._enabled:
                raise RuntimeError("Shoonya supervisor is stopped")
            staged = dict(self._descriptors)
            for item in batch:
                previous = staged.get(item.key)
                if previous is not None and previous != item:
                    raise ValueError(f"conflicting Shoonya instrument descriptor: {item.key.value}")
                staged[item.key] = item
            if len(staged) > self._max_tokens:
                raise ValueError(f"Shoonya subscription cap exceeded ({self._max_tokens})")
            self.registry.register_many(batch)
            self._descriptors = staged
            return len(batch)

    def subscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        # Keep registry ownership transactional with the adapter's own hard
        # validation. If the adapter rejects the batch, restore the previous
        # descriptor set so a failed subscribe cannot consume capacity.
        with self._lock:
            if not self._enabled:
                raise RuntimeError("Shoonya supervisor is stopped")
            previous = dict(self._descriptors)
        self.register(batch)
        try:
            count = self._adapter.subscribe(batch)
            with self._lock:
                should_start = self._enabled
            if should_start:
                self._adapter.start()
            return count
        except Exception:
            with self._lock:
                self._descriptors = previous
            raise

    def unsubscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        removed = self._adapter.unsubscribe(batch)
        with self._lock:
            for item in batch:
                self._descriptors.pop(item.key, None)
            if not self._descriptors:
                self._last_state = "IDLE_NO_SUBSCRIPTIONS"
        return removed

    def start(self) -> None:
        """Start only when an explicit subscription set has been registered."""
        with self._lock:
            if not self._enabled:
                raise RuntimeError("Shoonya supervisor is stopped")
            has_subscriptions = bool(self._adapter.snapshot()["subscriptions"])
        if has_subscriptions:
            self._adapter.start()

    def stop(self) -> None:
        with self._lock:
            if not self._enabled:
                return
            self._enabled = False
        self._adapter.stop()

    def _observe_tick(self, record: dict[str, Any]) -> None:
        # Shadow-only telemetry: intentionally no strategy, persistence, or order side effect.
        with self._lock:
            self._ticks_observed += 1
            self._last_tick_at = time()

    def _observe_order_update(self, event: dict[str, Any]) -> None:
        # Count lifecycle frames only. Never persist or act on broker order events here.
        with self._lock:
            self._order_events_observed += 1

    def _observe_state(self, state: str, reason: str | None) -> None:
        with self._lock:
            self._last_state = str(state)
            self._last_state_reason = reason
        if state in {"AUTH_FAILED", "ORDER_FEED_OVERFLOW", "STOP_TIMEOUT"}:
            app_logger.warning("Shoonya shadow feed state=%s reason=%s", state, reason)

    def snapshot(self) -> dict[str, Any]:
        adapter = self._adapter.snapshot()
        with self._lock:
            return {
                "broker": "shoonya",
                "mode": "shadow_only",
                "enabled": self._enabled,
                "state": self._last_state,
                "state_reason": self._last_state_reason,
                "registry_instruments": len(self._descriptors),
                "ticks_observed": self._ticks_observed,
                "order_events_observed": self._order_events_observed,
                "last_tick_at": self._last_tick_at,
                "adapter": adapter,
                "credentials_exposed": False,
                "order_routing_enabled": False,
                "strategy_fanout_enabled": False,
            }
