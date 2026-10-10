"""Common Angel One subscription manager for strategy-neutral market data.

Batch J owns the single-feed fan-out boundary: strategies register interest in
instruments, while this manager deduplicates broker subscriptions and forwards
ticks to consumer callbacks. Strategy code never opens a broker WebSocket.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Event, Lock, RLock, Thread, current_thread
import time
from typing import Any, Callable

from .contracts import InstrumentKey
from .registry import InstrumentRegistry, Subscription
from .normalizer import AngelOneTickNormalizer
from .websocket import MarketDataWebSocket
from app.core.logger import app_logger
from app.core.diagnostics import runtime_diagnostics


@dataclass(frozen=True)
class SocketGroup:
    mode: int
    shard: int = 0


class CommonWebSocketManager:
    """Maintain at most three shared Angel sessions and fan out ticks."""

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
        self._socket_tokens: dict[SocketGroup, set[tuple[int, str]]] = {}
        self._max_tokens_per_socket = 1000
        self._max_sockets = 3
        self._callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._record_callbacks: dict[str, Callable[[Any], None]] = {}
        self._normalizer = AngelOneTickNormalizer()
        self._lock = RLock()
        self._reconcile_lock = Lock()
        self._delivery_errors = 0
        self._normalizer_errors = 0
        self._last_normalizer_error: str | None = None
        self._ticks_received = 0
        self._runtime_started_at = time.time()
        self._ticks_by_exchange: dict[str, int] = defaultdict(int)
        self._last_tick: dict[str, Any] | None = None
        self._socket_created_at: dict[SocketGroup, float] = {}
        self._last_data_at: dict[SocketGroup, float] = {}
        # Identify socket generations so late Angel callbacks from a replaced
        # socket cannot be counted as live data for the new/absent group.
        self._socket_generation: dict[SocketGroup, int] = {}
        self._silent_feed_timeout_seconds = 30.0
        # A broker handshake can fail without firing on_error/on_close, leaving
        # MarketDataWebSocket.connecting stuck True forever. Bound that state.
        self._connect_timeout_seconds = 15.0
        self._route_index: dict[
            SocketGroup, dict[tuple[int, str], list[tuple[str, InstrumentKey]]]
        ] = {}
        self._recovery_stop = Event()
        self._recovery_thread: Thread | None = None
        self._recovery_interval_seconds = 5.0
        self._connect_failures = 0
        self._last_connect_failure = None
        self._recovery_attempts = 0
        self._last_recovery_at = None

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
        self._ensure_recovery_supervisor()
        self._reconcile()
        with self._lock:
            return tuple(sub for sub in self.registry.subscriptions() if consumer in sub.consumers)

    def unsubscribe(self, consumer: str, keys: list[InstrumentKey] | None = None) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            targets = keys if keys is not None else list(self.registry.active_keys())
            for key in targets:
                self.registry.unsubscribe(consumer, key)
        self._reconcile()

    def clear_consumer(self, consumer: str) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            self.registry.clear_consumer(consumer)
            self._callbacks.pop(consumer, None)
            self._record_callbacks.pop(consumer, None)
        self._reconcile()

    def _reconcile(self) -> None:
        """Pack subscriptions while keeping broker I/O outside the state lock."""
        with self._reconcile_lock:
            with self._lock:
                grouped: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
                for sub in self.registry.subscriptions():
                    exchange_type = self._resolve_exchange_type(sub.key)
                    grouped[sub.mode][exchange_type].add(sub.key.token.strip())

                desired_tokens: dict[SocketGroup, set[tuple[int, str]]] = {}
                route_index: dict[
                    SocketGroup, dict[tuple[int, str], list[tuple[str, InstrumentKey]]]
                ] = defaultdict(lambda: defaultdict(list))

                groups_by_mode: dict[int, list[tuple[int, str]]] = defaultdict(list)
                for mode, exchanges in grouped.items():
                    for exchange_type, tokens in exchanges.items():
                        for token in sorted(tokens):
                            groups_by_mode[mode].append((exchange_type, token))

                for mode in groups_by_mode:
                    groups_by_mode[mode].sort()
                    pairs = groups_by_mode[mode]
                    for index, pair in enumerate(pairs):
                        shard = SocketGroup(mode=mode, shard=index // self._max_tokens_per_socket)
                        desired_tokens.setdefault(shard, set()).add(pair)

                if len(desired_tokens) > self._max_sockets:
                    raise ValueError(
                        f"Angel One global WebSocket limit exceeded: {len(desired_tokens)} "
                        f"sessions required; maximum is {self._max_sockets}"
                    )

                for sub in self.registry.subscriptions():
                    exchange_type = self._resolve_exchange_type(sub.key)
                    pair = (exchange_type, sub.key.token.strip())
                    pairs = groups_by_mode[sub.mode]
                    index = pairs.index(pair)
                    shard = SocketGroup(mode=sub.mode, shard=index // self._max_tokens_per_socket)
                    for consumer in sub.consumers:
                        route_index[shard][pair].append((consumer, sub.key))

                self._route_index = {group: dict(tokens) for group, tokens in route_index.items()}
                existing = dict(self._sockets)
                previous_tokens = {group: set(tokens) for group, tokens in self._socket_tokens.items()}

            for group, pairs in desired_tokens.items():
                socket = existing.get(group)
                if socket is None:
                    socket = self._socket_factory()
                    subscriptions = self._group_subscriptions(pairs)
                    with self._lock:
                        generation = self._socket_generation.get(group, 0) + 1
                        self._socket_generation[group] = generation
                        # Register the socket before broker I/O. Angel's
                        # SmartWebSocketV2.connect() is asynchronous and may
                        # invoke on_open/on_data before connect() returns.
                        # Keeping the registry authoritative during that
                        # window prevents the recovery supervisor and callbacks
                        # from observing a false zero-socket state.
                        self._sockets[group] = socket
                        self._socket_tokens[group] = set(pairs)
                        now = time.monotonic()
                        self._socket_created_at[group] = now
                        self._last_data_at[group] = now
                    try:
                        socket.connect(
                            mode=group.mode,
                            subscriptions=subscriptions,
                            correlation_id=f"common-{group.mode}-{group.shard}",
                            on_data=lambda message, group=group, generation=generation: self._on_data(group, message, generation),
                        )
                    except Exception as exc:
                        with self._lock:
                            self._sockets.pop(group, None)
                            self._socket_tokens.pop(group, None)
                            self._socket_created_at.pop(group, None)
                            self._last_data_at.pop(group, None)
                            self._socket_generation.pop(group, None)
                            self._connect_failures += 1
                            self._last_connect_failure = {
                                "group": f"{group.mode}:{group.shard}",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                                "failed_at_ns": time.time_ns(),
                                "subscriptions": len(pairs),
                            }
                        runtime_diagnostics.record(
                            component="Common Market Feed",
                            error_type=type(exc).__name__,
                            message=str(exc),
                            context={"event": "socket_connect", "group": f"{group.mode}:{group.shard}", "subscriptions": len(pairs)},
                        )
                        app_logger.warning(
                            "Common feed socket connect failed group=%s error=%s: %s",
                            group, type(exc).__name__, exc,
                        )
                        self._close_socket_bounded(socket)
                        raise
                else:
                    removed = previous_tokens.get(group, set()) - pairs
                    added = pairs - previous_tokens.get(group, set())
                    if removed:
                        self._unsubscribe_pairs(socket, group.mode, removed)
                    if added:
                        self._subscribe_pairs(socket, group.mode, added)
                    with self._lock:
                        self._socket_tokens[group] = set(pairs)

            with self._lock:
                stale_groups = [group for group in self._sockets if group not in desired_tokens]
                stale_sockets = [self._sockets.pop(group) for group in stale_groups]
                for group in stale_groups:
                    self._socket_tokens.pop(group, None)
                    self._socket_created_at.pop(group, None)
                    self._last_data_at.pop(group, None)
                    self._socket_generation.pop(group, None)
            for socket in stale_sockets:
                self._close_socket_bounded(socket)

    def _ensure_recovery_supervisor(self) -> None:
        """Start one process-local self-healing loop for the shared broker feed."""
        with self._lock:
            if self._recovery_thread is not None and self._recovery_thread.is_alive():
                return
            self._recovery_stop.clear()
            self._recovery_thread = Thread(
                target=self._recovery_loop,
                name="common-ws-recovery",
                daemon=True,
            )
            self._recovery_thread.start()

    def _recovery_loop(self) -> None:
        while not self._recovery_stop.wait(self._recovery_interval_seconds):
            with self._lock:
                has_intent = bool(self.registry.subscriptions())
            if not has_intent:
                continue
            try:
                self.recover_disconnected(
                    min_age_seconds=0.0,
                    silent_age_seconds=self._silent_feed_timeout_seconds,
                )
            except Exception as exc:
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "automatic_recovery"},
                )
                app_logger.warning(
                    "Common feed automatic recovery failed; will retry: %s", exc
                )

    def recover_disconnected(
        self,
        *,
        min_age_seconds: float = 10.0,
        silent_age_seconds: float | None = None,
    ) -> int:
        """Replace stale/disconnected or connected-but-silent broker sockets."""
        now = time.monotonic()
        stale: list[tuple[SocketGroup, Any, set[tuple[int, str]], float]] = []
        with self._lock:
            for group, socket in self._sockets.items():
                connected = bool(getattr(socket, "connected", False))
                connecting = bool(getattr(socket, "connecting", False))
                created_at = self._socket_created_at.get(group, now)
                age = now - created_at
                # A normal async handshake gets a grace period, but a socket
                # that remains "connecting" beyond the deadline is wedged.
                # Replace it so the supervisor can retry with fresh auth/socket.
                if connecting and age < max(1.0, self._connect_timeout_seconds):
                    continue
                if age < max(1.0, float(min_age_seconds)):
                    continue
                tokens = set(self._socket_tokens.get(group, set()))
                if not tokens:
                    continue
                if connected and not connecting:
                    timeout = self._silent_feed_timeout_seconds if silent_age_seconds is None else silent_age_seconds
                    last_data_at = self._last_data_at.get(group, created_at)
                    if timeout < 0 or now - last_data_at < float(timeout):
                        continue
                stale.append((group, socket, tokens, created_at))

            # A partial reconcile can successfully create/store one shard and
            # fail while creating a later shard. The registry still contains
            # the full subscription intent, so detect missing shards explicitly
            # and let the next recovery cycle retry them.
            # Mirror _reconcile() exactly: sockets are sharded per mode
            # after exchange/token pairs are globally sorted for that mode.
            pairs_by_mode: dict[int, set[tuple[int, str]]] = defaultdict(set)
            for sub in self.registry.subscriptions():
                pairs_by_mode[sub.mode].add(
                    (self._resolve_exchange_type(sub.key), sub.key.token.strip())
                )
            expected_groups = 0
            for pairs in pairs_by_mode.values():
                expected_groups += (
                    len(pairs) + self._max_tokens_per_socket - 1
                ) // self._max_tokens_per_socket
            missing_groups = bool(pairs_by_mode) and len(self._sockets) < expected_groups

            for group, _, _, _ in stale:
                self._sockets.pop(group, None)
                self._socket_tokens.pop(group, None)
                self._socket_created_at.pop(group, None)
        if not stale and not missing_groups:
            return 0

        with self._lock:
            self._recovery_attempts += 1
            self._last_recovery_at = time.time()

        for _, socket, _, _ in stale:
            self._close_socket_bounded(socket)
        try:
            self._reconcile()
        except Exception:
            # Reconcile can fail transiently (broker auth/network/session limits).
            # Do not restore the closed stale socket: _reconcile() must see the
            # group as absent so the next recovery cycle can create a fresh
            # broker session. Preserve only the subscription intent.
            raise
        return len(stale)

    @staticmethod
    def _group_subscriptions(pairs: set[tuple[int, str]]) -> dict[int, list[str]]:
        result: dict[int, list[str]] = defaultdict(list)
        for exchange_type, token in sorted(pairs):
            result[exchange_type].append(token)
        return dict(result)

    @staticmethod
    def _by_exchange(pairs: set[tuple[int, str]]) -> dict[int, list[str]]:
        grouped: dict[int, list[str]] = defaultdict(list)
        for exchange_type, token in sorted(pairs):
            grouped[exchange_type].append(token)
        return dict(grouped)

    @classmethod
    def _subscribe_pairs(cls, socket: Any, mode: int, pairs: set[tuple[int, str]]) -> None:
        socket.subscribe_groups(cls._by_exchange(pairs), mode=mode)

    @classmethod
    def _unsubscribe_pairs(cls, socket: Any, mode: int, pairs: set[tuple[int, str]]) -> None:
        socket.unsubscribe_groups(cls._by_exchange(pairs))

    def _on_data(self, group: SocketGroup, message: Any, generation: int | None = None) -> None:
        if not isinstance(message, dict):
            return
        token = str(message.get("token") or message.get("symboltoken") or "").strip()
        if not token:
            return
        exchange_type_raw = message.get("exchange_type", message.get("exchangeType"))
        try:
            exchange_type = int(exchange_type_raw) if exchange_type_raw is not None else None
        except (TypeError, ValueError):
            exchange_type = None
        with self._lock:
            # Reject callbacks from an older socket after recovery has replaced
            # or removed that group. This prevents orphan ticks from masking a
            # zero-socket manager state.
            if generation is not None and self._socket_generation.get(group) != generation:
                return
            if group not in self._sockets:
                return
            if exchange_type is not None:
                matching = tuple(self._route_index.get(group, {}).get((exchange_type, token), ()))
            else:
                matching = tuple(
                    item for (route_exchange, route_token), items in self._route_index.get(group, {}).items()
                    if route_token == token for item in items
                )
            raw_callbacks = [self._callbacks.get(name) for name, _ in matching]
            record_callbacks = [(self._record_callbacks.get(name), key) for name, key in matching]
            if matching:
                self._ticks_received += 1
                self._ticks_by_exchange[str(exchange_type or "unknown")] += 1
                self._last_data_at[group] = time.monotonic()
        for callback in {id(cb): cb for cb in raw_callbacks if cb is not None}.values():
            try:
                callback(message)
            except Exception:
                with self._lock:
                    self._delivery_errors += 1
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type="CallbackDeliveryError",
                    message="Raw market-data consumer callback failed.",
                    context={"event": "delivery", "group": f"{group.mode}:{group.shard}", "token": token},
                )
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
                        "exchange_type": exchange_type or self._resolve_exchange_type(key),
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
            except (TypeError, ValueError) as exc:
                with self._lock:
                    self._normalizer_errors += 1
                    self._last_normalizer_error = f"{type(exc).__name__}: {exc}"
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "normalizer", "group": f"{group.mode}:{group.shard}", "token": token},
                )
                if self._normalizer_errors <= 5 or self._normalizer_errors % 1000 == 0:
                    app_logger.warning(
                        "Common feed normalizer rejected tick group=%s token=%s error=%s count=%s",
                        group, token, exc, self._normalizer_errors,
                    )
                continue
            except Exception as exc:
                with self._lock:
                    self._delivery_errors += 1
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "normalized_delivery", "group": f"{group.mode}:{group.shard}", "token": token},
                )

    @staticmethod
    def _close_socket_bounded(socket: Any, timeout: float = 2.0) -> None:
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
                "max_socket_sessions": self._max_sockets,
                "consumers": sorted(set(self._callbacks) | set(self._record_callbacks)),
                "delivery_errors": self._delivery_errors,
                "normalizer_errors": self._normalizer_errors,
                "last_normalizer_error": self._last_normalizer_error,
                "ticks_received": self._ticks_received,
                "runtime_started_at": self._runtime_started_at,
                "ticks_by_exchange_type": dict(self._ticks_by_exchange),
                "last_tick": dict(self._last_tick) if self._last_tick else None,
                "connected_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if bool(getattr(socket, "connected", False))
                ],
                "disconnected_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if not bool(getattr(socket, "connected", False))
                ],
                "connect_failures": self._connect_failures,
                "last_connect_failure": dict(self._last_connect_failure) if self._last_connect_failure else None,
                "recovery_attempts": self._recovery_attempts,
                "last_recovery_at": self._last_recovery_at,
            }

    def close(self) -> None:
        self._recovery_stop.set()
        with self._lock:
            recovery_thread = self._recovery_thread
            self._recovery_thread = None
            sockets = list(self._sockets.values())
            self._sockets.clear()
            self._socket_tokens.clear()
            self._socket_created_at.clear()
            self._last_data_at.clear()
            self._socket_generation.clear()
        for socket in sockets:
            self._close_socket_bounded(socket)
        if recovery_thread is not None and recovery_thread is not current_thread():
            recovery_thread.join(timeout=1.0)