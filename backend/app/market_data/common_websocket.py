"""Common Angel One subscription manager for strategy-neutral market data.

Batch J owns the single-feed fan-out boundary: strategies register interest in
instruments, while this manager deduplicates broker subscriptions and forwards
ticks to consumer callbacks. Strategy code never opens a broker WebSocket.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from .contracts import InstrumentKey
from .registry import InstrumentRegistry, Subscription
from .normalizer import AngelOneTickNormalizer
from .websocket import MarketDataWebSocket


@dataclass(frozen=True)
class SocketGroup:
    exchange_type: int
    mode: int


class CommonWebSocketManager:
    """Maintain one WebSocket per exchange-type/mode group and fan out ticks."""

    def __init__(
        self,
        registry: InstrumentRegistry | None = None,
        socket_factory: Callable[[], Any] = MarketDataWebSocket,
        exchange_type_resolver: Callable[[InstrumentKey], int] | None = None,
    ) -> None:
        self.registry = registry or InstrumentRegistry()
        self._socket_factory = socket_factory
        self._resolve_exchange_type = exchange_type_resolver or self._default_exchange_type
        self._sockets: dict[SocketGroup, Any] = {}
        self._callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._record_callbacks: dict[str, Callable[[Any], None]] = {}
        self._normalizer = AngelOneTickNormalizer()
        self._lock = RLock()

    @staticmethod
    def _default_exchange_type(key: InstrumentKey) -> int:
        value = key.exchange.strip().upper()
        mapping = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5, "NCDEX": 7}
        if value not in mapping:
            raise ValueError(f"unsupported exchange for Angel One WebSocket: {value}")
        return mapping[value]

    @staticmethod
    def _consumer_name(consumer: str) -> str:
        value = str(consumer).strip()
        if not value:
            raise ValueError("consumer is required")
        return value

    def register_callback(self, consumer: str, callback: Callable[[dict[str, Any]], None]) -> None:
        consumer = self._consumer_name(consumer)
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            self._callbacks[consumer] = callback

    def unregister_callback(self, consumer: str) -> None:
        with self._lock:
            self._callbacks.pop(self._consumer_name(consumer), None)

    def register_normalized_callback(self, consumer: str, callback: Callable[[Any], None]) -> None:
        """Register a callback receiving canonical MarketDataRecord values."""
        consumer = self._consumer_name(consumer)
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            self._record_callbacks[consumer] = callback

    def unregister_normalized_callback(self, consumer: str) -> None:
        with self._lock:
            self._record_callbacks.pop(self._consumer_name(consumer), None)

    def subscribe(self, consumer: str, keys: list[InstrumentKey], mode: int = 1) -> tuple[Subscription, ...]:
        consumer = self._consumer_name(consumer)
        if not keys:
            return ()
        with self._lock:
            for key in keys:
                if not isinstance(key, InstrumentKey):
                    raise TypeError("keys must contain InstrumentKey values")
                if self.registry.get(key) is None:
                    raise KeyError(f"instrument is not registered: {key.value}")
            for key in keys:
                self.registry.subscribe(consumer, key, mode=mode)
            self._reconcile_locked()
            return tuple(
                sub for sub in self.registry.subscriptions()
                if consumer in sub.consumers
            )

    def unsubscribe(self, consumer: str, keys: list[InstrumentKey] | None = None) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            targets = keys if keys is not None else list(self.registry.active_keys())
            for key in targets:
                self.registry.unsubscribe(consumer, key)
            self._reconcile_locked()

    def clear_consumer(self, consumer: str) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            self.registry.clear_consumer(consumer)
            self._callbacks.pop(consumer, None)
            self._record_callbacks.pop(consumer, None)
            self._reconcile_locked()

    def _reconcile_locked(self) -> None:
        desired: dict[SocketGroup, set[str]] = defaultdict(set)
        for sub in self.registry.subscriptions():
            group = SocketGroup(self._resolve_exchange_type(sub.key), sub.mode)
            desired[group].add(sub.key.token.strip())

        for group, tokens in desired.items():
            socket = self._sockets.get(group)
            if socket is None:
                socket = self._socket_factory()
                self._sockets[group] = socket
                socket.connect(
                    exchange_type=group.exchange_type,
                    tokens=sorted(tokens),
                    mode=group.mode,
                    correlation_id=f"common-{group.exchange_type}-{group.mode}",
                    on_data=lambda message, group=group: self._on_data(group, message),
                )
            else:
                socket.subscribe(sorted(tokens), mode=group.mode)

        for group in list(self._sockets):
            if group not in desired:
                socket = self._sockets.pop(group)
                socket.close()

    def _on_data(self, group: SocketGroup, message: Any) -> None:
        if not isinstance(message, dict):
            return
        token = str(message.get("token") or message.get("symboltoken") or "").strip()
        if not token:
            return
        matching: list[tuple[str, InstrumentKey]] = []
        for sub in self.registry.subscriptions():
            if (
                sub.key.token.strip() == token
                and SocketGroup(self._resolve_exchange_type(sub.key), sub.mode) == group
            ):
                matching.extend((consumer, sub.key) for consumer in sub.consumers)
        with self._lock:
            raw_callbacks = [self._callbacks.get(name) for name, _ in matching]
            record_callbacks = [(self._record_callbacks.get(name), key) for name, key in matching]
        for callback in {id(cb): cb for cb in raw_callbacks if cb is not None}.values():
            callback(message)
        for callback, key in record_callbacks:
            if callback is None:
                continue
            descriptor = self.registry.get(key)
            if descriptor is None:
                continue
            try:
                callback(self._normalizer.normalize(descriptor, message))
            except (TypeError, ValueError):
                continue

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "subscriptions": len(self.registry.subscriptions()),
                "active_instruments": len(self.registry.active_keys()),
                "socket_groups": len(self._sockets),
                "consumers": sorted(self._callbacks),
            }

    def close(self) -> None:
        with self._lock:
            sockets = list(self._sockets.values())
            self._sockets.clear()
        for socket in sockets:
            socket.close()
