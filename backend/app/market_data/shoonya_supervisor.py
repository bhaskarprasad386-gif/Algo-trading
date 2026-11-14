"""Default-off Shoonya runtime owner with an isolated instrument registry.

This supervisor deliberately does not reuse the Angel One shared feed or route
records into trading/scanner consumers. It is a shadow diagnostics boundary
until a separately reviewed provider-selection integration is approved.
"""
from __future__ import annotations

from threading import RLock
from time import time
from typing import Any, Callable, Iterable

from app.core.logger import app_logger
from .registry import InstrumentDescriptor, InstrumentRegistry
from .contracts import MarketDataRecord
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
        frame_timeout_seconds: float = 30.0,
        auth_timeout_seconds: float = 15.0,
        websocket_factory: Any | None = None,
        on_tick_observation: Callable[[MarketDataRecord, int, bool, int], None] | None = None,
    ) -> None:
        if not 1 <= int(max_tokens) <= 500:
            raise ValueError("max_tokens must be between 1 and 500")
        self.registry = InstrumentRegistry()
        self._lock = RLock()
        self._operation_lock = RLock()
        self._enabled = True
        self._max_tokens = int(max_tokens)
        self._descriptors: dict[Any, InstrumentDescriptor] = {}
        self._last_tick_at: float | None = None
        self._ticks_observed = 0
        self._order_events_observed = 0
        self._last_state = "IDLE_NO_SUBSCRIPTIONS"
        self._last_state_reason: str | None = None
        self._secret_values = tuple(value for value in (str(uid).strip(), str(actid).strip(), str(access_token).strip()) if value)
        self._adapter = ShoonyaWebSocket(
            uid=uid,
            actid=actid,
            access_token=access_token,
            on_tick=self._observe_tick,
            on_order_update=self._observe_order_update,
            on_state=self._observe_state,
            on_tick_observation=on_tick_observation,
            websocket_factory=websocket_factory,
            max_dispatch_queue=max_dispatch_queue,
            reconnect_min_seconds=reconnect_min_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
            frame_timeout_seconds=frame_timeout_seconds,
            auth_timeout_seconds=auth_timeout_seconds,
        )

    def register(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        with self._operation_lock:
            with self._lock:
                if not self._enabled:
                    raise RuntimeError("Shoonya supervisor is stopped")
                staged = dict(self._descriptors)
                adapter_keys = {
                    (value.key.exchange.strip().upper(), value.key.token.strip()): value
                    for value in staged.values()
                }
                for item in batch:
                    adapter_key = (item.key.exchange.strip().upper(), item.key.token.strip())
                    adapter_previous = adapter_keys.get(adapter_key)
                    if adapter_previous is not None and adapter_previous != item:
                        raise ValueError(f"conflicting Shoonya adapter token descriptor: {adapter_key}")
                    previous = staged.get(item.key)
                    if previous is not None and previous != item:
                        raise ValueError(f"conflicting Shoonya instrument descriptor: {item.key.value}")
                    staged[item.key] = item
                    adapter_keys[adapter_key] = item
                if len(staged) > self._max_tokens:
                    raise ValueError(f"Shoonya subscription cap exceeded ({self._max_tokens})")
                self.registry.register_many(batch)
                self._descriptors = staged
                return len(batch)

    def subscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        with self._operation_lock:
            with self._lock:
                if not self._enabled:
                    raise RuntimeError("Shoonya supervisor is stopped")
                previous = dict(self._descriptors)
            added_descriptors = tuple(item for item in batch if item.key not in previous)
            self.register(batch)
            adapter_subscribed = False
            start_attempted = False
            try:
                count = self._adapter.subscribe(batch)
                adapter_subscribed = True
                start_attempted = True
                self._adapter.start()
                return count
            except Exception:
                # A thread may have started before a later Thread.start() fails.
                # Stop any partially-created workers before rolling back state.
                if start_attempted:
                    try:
                        self._adapter.stop()
                    except Exception as cleanup_error:
                        app_logger.error("Shoonya start-failure stop cleanup failed: {}", self._safe_reason(str(cleanup_error)))
                # Roll back only instruments introduced by this operation.
                # The request may mix existing subscriptions with new ones;
                # removing the full batch here would silently drop valid feeds.
                if adapter_subscribed and added_descriptors:
                    try:
                        self._adapter.unsubscribe(added_descriptors)
                    except Exception as cleanup_error:
                        app_logger.error("Shoonya subscription rollback cleanup failed: {}", self._safe_reason(str(cleanup_error)))
                with self._lock:
                    added_keys = set(self._descriptors) - set(previous)
                    self._descriptors = previous
                    self.registry.unregister_many(added_keys)
                raise

    def unsubscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch:
            return 0
        with self._operation_lock:
            with self._lock:
                stored: list[InstrumentDescriptor] = []
                for item in batch:
                    existing = self._descriptors.get(item.key)
                    if existing is None:
                        adapter_key = (item.key.exchange.strip().upper(), item.key.token.strip())
                        if any((value.key.exchange.strip().upper(), value.key.token.strip()) == adapter_key
                               for value in self._descriptors.values()):
                            raise ValueError(f"conflicting Shoonya unsubscribe descriptor: {adapter_key}")
                        continue
                    if existing != item:
                        raise ValueError(f"conflicting Shoonya unsubscribe descriptor: {item.key.value}")
                    stored.append(existing)
            if not stored:
                return 0
            removed = self._adapter.unsubscribe(stored)
            with self._lock:
                for item in stored:
                    self._descriptors.pop(item.key, None)
                self.registry.unregister_many(item.key for item in stored)
                became_idle = not self._descriptors
            if became_idle:
                # Release the broker socket and all adapter workers as soon as
                # the final desired subscription is removed. Keep the supervisor
                # enabled so a later explicit subscribe can start a fresh session.
                self._adapter.stop()
                with self._lock:
                    self._last_state = "IDLE_NO_SUBSCRIPTIONS"
                    self._last_state_reason = None
            return removed

    def start(self) -> None:
        """Start only when an explicit subscription set has been registered."""
        with self._operation_lock:
            with self._lock:
                if not self._enabled:
                    raise RuntimeError("Shoonya supervisor is stopped")
                has_subscriptions = bool(self._adapter.snapshot()["subscriptions"])
            if has_subscriptions:
                self._adapter.start()

    def stop(self) -> None:
        with self._operation_lock:
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

    def _safe_reason(self, reason: str | None) -> str | None:
        if not reason:
            return None
        safe = str(reason)
        for secret in self._secret_values:
            safe = safe.replace(secret, "[REDACTED]")
        return safe[:240]

    def _observe_state(self, state: str, reason: str | None) -> None:
        safe_reason = self._safe_reason(reason)
        with self._lock:
            self._last_state = str(state)
            self._last_state_reason = safe_reason
        if state in {"AUTH_FAILED", "ORDER_FEED_OVERFLOW", "STOP_TIMEOUT"}:
            app_logger.warning("Shoonya shadow feed state={} reason={}", state, safe_reason)

    def snapshot(self) -> dict[str, Any]:
        adapter = self._adapter.snapshot()
        if adapter.get("last_error"):
            adapter["last_error"] = self._safe_reason(adapter["last_error"])
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
