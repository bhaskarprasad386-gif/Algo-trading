"""Common Angel One subscription manager for strategy-neutral market data.

Batch J owns the single-feed fan-out boundary: strategies register interest in
instruments, while this manager deduplicates broker subscriptions and forwards
ticks to consumer callbacks. Strategy code never opens a broker WebSocket.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock, Thread
from typing import Any, Callable

from .contracts import InstrumentKey
from .registry import InstrumentRegistry, Subscription
from .normalizer import AngelOneTickNormalizer
from .websocket import MarketDataWebSocket


@dataclass(frozen=True)
class SocketGroup:
    exchange_type: int
    mode: int
    shard: int = 0


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
        self._socket_tokens: dict[SocketGroup, set[str]] = {}
        self._max_tokens_per_socket = 1000
        self._max_sockets_per_group = 3
        self._callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._record_callbacks: dict[str, Callable[[Any], None]] = {}
        self._normalizer = AngelOneTickNormalizer()
        self._lock = RLock()
        self._delivery_errors = 0
        self._ticks_received = 0
        self._ticks_by_exchange: dict[str, int] = defaultdict(int)
        self._last_tick: dict[str, Any] | None = None
        # Pre-index subscriptions by broker socket group + token so each tick
        # does not scan the entire subscription registry.
        self._route_index: dict[SocketGroup, dict[str, list[tuple[str, InstrumentKey]]]] = {}

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
        """Reconcile subscriptions within Angel One's 1000-token/3-session limits."""
        route_index: dict[SocketGroup, dict[str, list[tuple[str, InstrumentKey]]]] = defaultdict(lambda: defaultdict(list))
        grouped: dict[tuple[int, int], set[str]] = defaultdict(set)
        for sub in self.registry.subscriptions():
            exchange_type = self._resolve_exchange_type(sub.key)
            grouped[(exchange_type, sub.mode)].add(sub.key.token.strip())

        for sub in self.registry.subscriptions():
            exchange_type = self._resolve_exchange_type(sub.key)
            group_key = (exchange_type, sub.mode)
            tokens = sorted(grouped[group_key])
            shard_index = tokens.index(sub.key.token.strip()) // self._max_tokens_per_socket
            shard = SocketGroup(exchange_type, sub.mode, shard_index)
            for consumer in sub.consumers:
                route_index[shard][sub.key.token.strip()].append((consumer, sub.key))

        desired_tokens: dict[SocketGroup, set[str]] = {}
        for (exchange_type, mode), tokens_set in grouped.items():
            tokens = sorted(tokens_set)
            shard_count = (len(tokens) + self._max_tokens_per_socket - 1) // self._max_tokens_per_socket
            if shard_count > self._max_sockets_per_group:
                raise ValueError(
                    f"Angel One subscription limit exceeded for exchange={exchange_type} mode={mode}: "
                    f"{len(tokens)} tokens requires {shard_count} sockets; maximum is {self._max_sockets_per_group}"
                )
            for shard_index in range(shard_count):
                shard = SocketGroup(exchange_type, mode, shard_index)
                desired_tokens[shard] = set(tokens[
                    shard_index * self._max_tokens_per_socket:
                    (shard_index + 1) * self._max_tokens_per_socket
                ])

        self._route_index = {group: dict(tokens) for group, tokens in route_index.items()}

        for group, tokens in desired_tokens.items():
            socket = self._sockets.get(group)
            previous_tokens = self._socket_tokens.get(group, set())
            if socket is None:
                socket = self._socket_factory()
                self._sockets[group] = socket
                self._socket_tokens[group] = set(tokens)
                socket.connect(
                    exchange_type=group.exchange_type,
                    tokens=sorted(tokens),
                    mode=group.mode,
                    correlation_id=f"common-{group.exchange_type}-{group.mode}-{group.shard}",
                    on_data=lambda message, group=group: self._on_data(group, message),
                )
            else:
                removed = sorted(previous_tokens - tokens)
                added = sorted(tokens - previous_tokens)
                if removed:
                    socket.unsubscribe(removed)
                if added:
                    socket.subscribe(added, mode=group.mode)
                self._socket_tokens[group] = set(tokens)

        for group in list(self._sockets):
            if group not in desired_tokens:
                socket = self._sockets.pop(group)
                self._socket_tokens.pop(group, None)
                self._close_socket_bounded(socket)

    def _on_data(self, group: SocketGroup, message: Any) -> None:
        if not isinstance(message, dict):
            return
        token = str(message.get("token") or message.get("symboltoken") or "").strip()
        if not token:
            return
        with self._lock:
            matching = tuple(self._route_index.get(group, {}).get(token, ()))
            raw_callbacks = [self._callbacks.get(name) for name, _ in matching]
            record_callbacks = [(self._record_callbacks.get(name), key) for name, key in matching]
            if matching:
                self._ticks_received += 1
                self._ticks_by_exchange[str(group.exchange_type)] += 1
        for callback in {id(cb): cb for cb in raw_callbacks if cb is not None}.values():
            try:
                callback(message)
            except Exception:
                with self._lock:
                    self._delivery_errors += 1
        for callback, key in record_callbacks:
            if callback is None:
                continue
            descriptor = self.registry.get(key)
            if descriptor is None:
                continue
            try:
                record = self._normalizer.normalize(descriptor, message)
                with self._lock:
                    self._last_tick = {
                        "exchange_type": group.exchange_type,
                        "exchange": record.instrument.exchange,
                        "segment": record.instrument.segment,
                        "token": record.instrument.token,
                        "symbol": record.symbol,
                        "timestamp_ns": record.timestamp_ns,
                        "ltp": record.ltp,
                        "bid": record.bid,
                        "ask": record.ask,
                    }
                callback(record)
            except (TypeError, ValueError):
                continue
            except Exception:
                with self._lock:
                    self._delivery_errors += 1

    @staticmethod
    def _close_socket_bounded(socket: Any, timeout: float = 2.0) -> None:
        """Keep application shutdown bounded even if broker close blocks."""
        def _close() -> None:
            try:
                socket.close()
            except Exception:
                pass

        worker = Thread(target=_close, name="common-ws-close", daemon=True)
        worker.start()
        worker.join(timeout=max(0.1, float(timeout)))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            sockets = dict(self._sockets)
            return {
                "subscriptions": len(self.registry.subscriptions()),
                "active_instruments": len(self.registry.active_keys()),
                "socket_groups": len(sockets),
                "consumers": sorted(set(self._callbacks) | set(self._record_callbacks)),
                "delivery_errors": self._delivery_errors,
                "ticks_received": self._ticks_received,
                "ticks_by_exchange_type": dict(self._ticks_by_exchange),
                "last_tick": dict(self._last_tick) if self._last_tick else None,
                "connected_groups": [
                    f"{group.exchange_type}:{group.mode}"
                    for group, socket in sockets.items()
                    if bool(getattr(socket, "connected", False))
                ],
                "disconnected_groups": [
                    f"{group.exchange_type}:{group.mode}"
                    for group, socket in sockets.items()
                    if not bool(getattr(socket, "connected", False))
                ],
            }

    def close(self) -> None:
        with self._lock:
            sockets = list(self._sockets.values())
            self._sockets.clear()
            self._socket_tokens.clear()
        for socket in sockets:
            self._close_socket_bounded(socket)
