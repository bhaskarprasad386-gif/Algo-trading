"""Common Angel One subscription manager for strategy-neutral market data.

Batch J owns the single-feed fan-out boundary: strategies register interest in
instruments, while this manager deduplicates broker subscriptions and forwards
ticks to consumer callbacks. Strategy code never opens a broker WebSocket.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from math import isfinite
from threading import Event, Lock, RLock, Thread, current_thread
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

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


# Broker-wide cooldown/admission also covers direct collectors.
_SHARED_BROKER_COOLDOWN_STATE_LOCK = Lock()
_SHARED_BROKER_CONNECT_ADMISSION_LOCK = Lock()
_SHARED_BROKER_COOLDOWN_UNTIL = 0.0
_SHARED_BROKER_MIN_CONNECT_INTERVAL_SECONDS = 2.0
_SHARED_BROKER_LAST_CONNECT_STARTED_AT = 0.0


def _wait_for_shared_broker_connect_admission(
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Wait under the admission lock until cooldown and global pacing both allow connect."""
    global _SHARED_BROKER_LAST_CONNECT_STARTED_AT
    while should_stop is None or not should_stop():
        with _SHARED_BROKER_COOLDOWN_STATE_LOCK:
            now = time.monotonic()
            cooldown_remaining = max(0.0, _SHARED_BROKER_COOLDOWN_UNTIL - now)
            pacing_remaining = max(
                0.0,
                _SHARED_BROKER_LAST_CONNECT_STARTED_AT
                + _SHARED_BROKER_MIN_CONNECT_INTERVAL_SECONDS
                - now,
            )
            wait_for = max(cooldown_remaining, pacing_remaining)
            if wait_for <= 0.0:
                _SHARED_BROKER_LAST_CONNECT_STARTED_AT = now
                return True
        time.sleep(min(max(wait_for, 0.01), 0.1))
    return False


def _mark_socket_auth_stale(socket: Any) -> None:
    """Mark auth stale without letting an optional socket hook break recovery."""
    marker = getattr(socket, "mark_session_stale", None)
    if callable(marker):
        try:
            marker()
        except Exception as exc:
            app_logger.warning("Could not mark Angel session stale after broker limit error: %s", exc)


def shared_broker_cooldown_remaining() -> float:
    with _SHARED_BROKER_COOLDOWN_STATE_LOCK:
        return max(0.0, _SHARED_BROKER_COOLDOWN_UNTIL - time.monotonic())


def apply_shared_broker_cooldown(reason: Any, seconds: float = 60.0) -> bool:
    global _SHARED_BROKER_COOLDOWN_UNTIL
    text = str(reason).strip().lower()
    if not any(term in text for term in ("429", "connection limit exceeded", "too many connections", "maximum connection")):
        return False
    with _SHARED_BROKER_COOLDOWN_STATE_LOCK:
        _SHARED_BROKER_COOLDOWN_UNTIL = max(_SHARED_BROKER_COOLDOWN_UNTIL, time.monotonic() + max(0.0, seconds))
        remaining = max(0.0, _SHARED_BROKER_COOLDOWN_UNTIL - time.monotonic())
    app_logger.warning("Angel shared connection-limit cooldown activated for %.1fs reason=%s", remaining, reason)
    return True


def connect_with_shared_broker_admission(
    socket: Any,
    connect_kwargs: dict[str, Any],
    should_stop: Callable[[], bool] | None = None,
    cooldown_seconds: float = 60.0,
) -> bool:
    """Run a direct Angel connect only after the broker-wide 429 cooldown."""
    with _SHARED_BROKER_CONNECT_ADMISSION_LOCK:
        if not _wait_for_shared_broker_connect_admission(should_stop):
            return False
        # MarketDataWebSocket.connect returns on handshake success/failure; its
        # run_forever loop now lives in a separate tracked transport thread.
        if should_stop is not None and should_stop():
            return False
        try:
            socket.connect(**connect_kwargs)
        except Exception as exc:
            if apply_shared_broker_cooldown(exc, cooldown_seconds):
                _mark_socket_auth_stale(socket)
            raise
    return True


_INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _is_regular_trading_window(now: datetime | None = None) -> bool:
    """Whether NSE's regular weekday session is scheduled by the clock.

    Tick silence is evidence of a broken feed only during regular market hours.
    Outside that window, a healthy WebSocket can legitimately receive no ticks.
    This is deliberately a clock-window guard, not an exchange-holiday calendar.
    """
    current = (now or datetime.now(_INDIA_TIMEZONE)).astimezone(_INDIA_TIMEZONE)
    local_time = current.timetz().replace(tzinfo=None)
    return current.weekday() < 5 and datetime_time(9, 15) <= local_time <= datetime_time(15, 30)


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
        self._reconcile_lock = RLock()
        # State and admission are shared across all Angel WebSocket components.
        self._broker_cooldown_seconds = 60.0
        self._delivery_errors = 0
        self._missing_callback_routes = 0
        self._missing_descriptor_records = 0
        self._normalized_records_emitted = 0
        # Distinguish successful normalization from a record actually accepted by its consumer.
        self._normalized_records_delivered = 0
        self._raw_callback_attempts = 0
        self._raw_callback_successes = 0
        self._missing_exchange_type_frames = 0
        self._ambiguous_token_frames = 0
        self._normalized_delivery_errors = 0
        self._last_delivery_error: str | None = None
        self._normalizer_errors = 0
        self._last_normalizer_error: str | None = None
        self._ticks_received = 0
        # Count every current-generation callback before parsing/routing. This
        # separates "broker sent no frames" from "frames arrived but could not
        # be mapped to a registered instrument".
        self._raw_frames_received = 0
        self._invalid_frame_types = 0
        self._missing_token_frames = 0
        self._invalid_exchange_type_frames = 0
        self._unmatched_frames = 0
        self._exchange_mismatch_frames = 0
        self._last_raw_frame_at: float | None = None
        self._runtime_started_at = time.time()
        self._ticks_by_exchange: dict[str, int] = defaultdict(int)
        self._last_tick: dict[str, Any] | None = None
        self._socket_created_at: dict[SocketGroup, float] = {}
        # Keep transport activity, successful routing, and consumer delivery as
        # separate clocks; a mapping/callback issue must not masquerade as a dead socket.
        self._last_raw_frame_at_by_group: dict[SocketGroup, float] = {}
        self._last_successful_route_at: dict[SocketGroup, float] = {}
        self._last_data_at: dict[SocketGroup, float] = {}
        # None means this socket generation has not delivered a tick to any consumer yet.
        self._last_successful_delivery_at: dict[SocketGroup, float | None] = {}
        # Identify socket generations so late Angel callbacks from a replaced
        # socket cannot be counted as live data for the new/absent group.
        self._socket_generation: dict[SocketGroup, int] = {}
        # Keep generation counters monotonic after an active socket is removed.
        self._socket_generation_counter: dict[SocketGroup, int] = {}
        # One connect worker per current socket generation. Recovery and the
        # periodic supervisor can race to request the same generation.
        self._connect_started_generation: dict[SocketGroup, int] = {}
        # Track sockets waiting for broker-wide connect admission so reconcile
        # does not mistake a queued handshake for a confirmed disconnect.
        self._connect_pending_generation: dict[SocketGroup, int] = {}
        self._silent_feed_timeout_seconds = 30.0
        # A broker handshake can fail without firing on_error/on_close, leaving
        # MarketDataWebSocket.connecting stuck True forever. Bound that state.
        self._connect_timeout_seconds = 15.0
        # Never let a broker connect call hold reconciliation hostage. A call
        # that does not return promptly continues in a daemon worker while the
        # recovery supervisor remains free to replace its stale socket.
        self._connect_call_wait_seconds = 0.25
        # Subscription APIs can block inside the SDK too; bound those calls so
        # they cannot monopolize the reconciliation lock and stop recovery.
        self._socket_call_wait_seconds = 2.0
        self._route_index: dict[
            SocketGroup, dict[tuple[int, str], list[tuple[str, InstrumentKey]]]
        ] = {}
        self._recovery_stop = Event()
        self._recovery_thread: Thread | None = None
        self._closed = False
        self._recovery_interval_seconds = 5.0
        self._connect_failures = 0
        self._last_connect_failure = None
        self._socket_failure_events = 0
        self._last_socket_failure = None
        # Coalesce callback-triggered recovery per shard. The periodic supervisor
        # remains the fallback, but duplicate SDK callbacks must not spawn a
        # burst of close/recovery workers for the same failed generation.
        self._failure_recovery_groups: set[SocketGroup] = set()
        # Retired transports remain visible until their close worker actually exits.
        # Store metadata only; never retain broker frames or authentication payloads.
        self._retired_transports: dict[str, dict[str, Any]] = {}
        self._recovery_attempts = 0
        self._last_recovery_at = None

    def _create_socket(self) -> Any:
        """Create a socket whose reconnect policy is owned by this manager."""
        if self._socket_factory is MarketDataWebSocket:
            # The shared manager must be the sole reconnect supervisor; the
            # wrapper's private retry loop could otherwise open orphan sessions
            # after this manager has replaced a failed socket.
            return self._socket_factory(auto_reconnect=False)
        return self._socket_factory()

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
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in (1, 2, 3, 4):
            raise ValueError("mode must be one of Angel One WebSocket modes 1, 2, 3, or 4")
        with self._lock:
            if self._closed:
                raise RuntimeError("CommonWebSocketManager is closed")
            before = {
                sub.key: sub.mode
                for sub in self.registry.subscriptions()
                if consumer in sub.consumers
            }
            for key in keys:
                if not isinstance(key, InstrumentKey):
                    raise TypeError("keys must contain InstrumentKey values")
                if self.registry.get(key) is None:
                    raise KeyError(f"instrument is not registered: {key.value}")
            for key in keys:
                self.registry.subscribe(consumer, key, mode=mode)
            after = {
                sub.key: sub.mode
                for sub in self.registry.subscriptions()
                if consumer in sub.consumers
            }
            added = len(after.keys() - before.keys())
            removed = len(before.keys() - after.keys())
            changed_mode = sum(1 for key in before.keys() & after.keys() if before[key] != after[key])
            if added or removed or changed_mode:
                app_logger.info(
                    f"Common feed subscription mutation operation=subscribe consumer={consumer} "
                    f"added={added} removed={removed} mode_changed={changed_mode} "
                    f"consumer_subscriptions={len(after)} total_subscriptions={len(self.registry.subscriptions())}"
                )
        self._ensure_recovery_supervisor()
        self._reconcile()
        with self._lock:
            return tuple(sub for sub in self.registry.subscriptions() if consumer in sub.consumers)

    def unsubscribe(self, consumer: str, keys: list[InstrumentKey] | None = None) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            before = {
                sub.key: sub.mode
                for sub in self.registry.subscriptions()
                if consumer in sub.consumers
            }
            targets = keys if keys is not None else list(self.registry.active_keys())
            for key in targets:
                self.registry.unsubscribe(consumer, key)
            after = {
                sub.key: sub.mode
                for sub in self.registry.subscriptions()
                if consumer in sub.consumers
            }
            removed = len(before.keys() - after.keys())
            changed_mode = sum(1 for key in before.keys() & after.keys() if before[key] != after[key])
            if removed or changed_mode:
                app_logger.info(
                    f"Common feed subscription mutation operation=unsubscribe consumer={consumer} "
                    f"added=0 removed={removed} mode_changed={changed_mode} "
                    f"consumer_subscriptions={len(after)} total_subscriptions={len(self.registry.subscriptions())}"
                )
        self._reconcile()

    def clear_consumer(self, consumer: str) -> None:
        consumer = self._consumer_name(consumer)
        with self._lock:
            before = sum(1 for sub in self.registry.subscriptions() if consumer in sub.consumers)
            removed = self.registry.clear_consumer(consumer)
            self._callbacks.pop(consumer, None)
            self._record_callbacks.pop(consumer, None)
            total = len(self.registry.subscriptions())
            if removed:
                app_logger.info(
                    f"Common feed subscription mutation operation=clear_consumer consumer={consumer} "
                    f"removed={removed} consumer_subscriptions_before={before} total_subscriptions_after={total}"
                )
        self._reconcile()

    def _reconcile(self) -> None:
        """Pack subscriptions while keeping broker I/O outside the state lock."""
        with self._reconcile_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("CommonWebSocketManager is closed")
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

                # Free obsolete groups before opening replacement/missing groups.
                # Otherwise a recovery from an unexpected group can temporarily
                # exceed Angel One's global three-session limit.
                stale_groups = [group for group in self._sockets if group not in desired_tokens]
                if stale_groups:
                    consumer_counts: dict[str, int] = defaultdict(int)
                    for sub in self.registry.subscriptions():
                        for consumer in sub.consumers:
                            consumer_counts[consumer] += 1
                    app_logger.info(
                        f"Common feed reconcile obsolete groups={sorted(f'{group.mode}:{group.shard}' for group in stale_groups)} "
                        f"desired_groups={sorted(f'{group.mode}:{group.shard}' for group in desired_tokens)} "
                        f"existing_groups={sorted(f'{group.mode}:{group.shard}' for group in self._sockets)} "
                        f"subscriptions={len(self.registry.subscriptions())} "
                        f"consumer_counts={dict(sorted(consumer_counts.items()))}"
                    )
                stale_sockets = [self._sockets.pop(group) for group in stale_groups]
                for group in stale_groups:
                    self._socket_tokens.pop(group, None)
                    self._socket_created_at.pop(group, None)
                    self._last_data_at.pop(group, None)
                    self._last_raw_frame_at_by_group.pop(group, None)
                    self._last_successful_route_at.pop(group, None)
                    self._last_successful_delivery_at.pop(group, None)
                    self._socket_generation.pop(group, None)

            for group, socket in zip(stale_groups, stale_sockets):
                def _reconcile_after_obsolete_close(obsolete_group=group):
                    try:
                        with self._reconcile_lock:
                            self._reconcile()
                    except Exception as exc:
                        app_logger.warning(
                            "Common feed reconcile after obsolete close failed group=%s: %s",
                            f"{obsolete_group.mode}:{obsolete_group.shard}", exc,
                        )
                self._close_socket_tracked(
                    socket, group=group,
                    generation=self._socket_generation_counter.get(group),
                    reason="obsolete_group", on_finished=_reconcile_after_obsolete_close,
                )

            connect_errors: list[Exception] = []
            for group, pairs in desired_tokens.items():
                socket = existing.get(group)
                if socket is None:
                    # The failed generation is still being closed. Do not admit
                    # its replacement until the bounded close attempt completes.
                    with self._lock:
                        if group in self._failure_recovery_groups:
                            continue
                if socket is not None:
                    # A transport can become unusable before its SDK wrapper
                    # publishes the close callback. Never send incremental
                    # subscribe/unsubscribe operations to a known-dead socket.
                    with self._lock:
                        created_at = self._socket_created_at.get(group, time.monotonic())
                    # Test doubles and third-party wrappers may not expose a
                    # connection-state property. Treat an unknown state as
                    # unknown, not as a confirmed disconnect; otherwise every
                    # reconcile can discard a healthy shared socket.
                    connected_state = getattr(socket, "connected", None)
                    connecting = bool(getattr(socket, "connecting", False))
                    connect_timed_out = connecting and (
                        time.monotonic() - created_at >= self._connect_timeout_seconds
                    )
                    with self._lock:
                        pending_generation = self._connect_pending_generation.get(group)
                        current_generation = self._socket_generation.get(group)
                    connect_pending = (
                        pending_generation == current_generation
                        and time.monotonic() - created_at < self._connect_timeout_seconds
                    )
                    disconnected = connected_state is False and (
                        not connecting or connect_timed_out
                    ) and not connect_pending
                    if disconnected:
                        with self._lock:
                            if self._sockets.get(group) is socket:
                                self._sockets.pop(group, None)
                                self._socket_tokens.pop(group, None)
                                self._socket_created_at.pop(group, None)
                                self._last_data_at.pop(group, None)
                                self._last_raw_frame_at_by_group.pop(group, None)
                                self._last_successful_route_at.pop(group, None)
                                self._last_successful_delivery_at.pop(group, None)
                                self._socket_generation.pop(group, None)
                                # Retirement is also a recovery lifecycle: block a
                                # replacement while the old transport is still closing.
                                self._failure_recovery_groups.add(group)
                        def _resume_after_retired_close(retired_group=group):
                            try:
                                with self._reconcile_lock:
                                    with self._lock:
                                        self._failure_recovery_groups.discard(retired_group)
                                    self._reconcile()
                            except Exception as exc:
                                app_logger.warning(
                                    "Common feed reconcile after retired close failed group=%s: %s",
                                    f"{retired_group.mode}:{retired_group.shard}", exc,
                                )
                        close_worker = self._close_socket_tracked(
                            socket, group=group,
                            generation=self._socket_generation_counter.get(group),
                            reason="reconcile_disconnected",
                            on_finished=_resume_after_retired_close,
                        )
                        socket = None
                        if close_worker.is_alive():
                            continue
                        with self._lock:
                            self._failure_recovery_groups.discard(group)
                if socket is None:
                    # A timed-out close on any retired shard may still occupy
                    # one of Angel's global sessions. Do not open a replacement
                    # until all known retired close workers have exited.
                    with self._lock:
                        if self._retired_transports:
                            continue
                    socket = self._create_socket()
                    subscriptions = self._group_subscriptions(pairs)
                    with self._lock:
                        generation = self._socket_generation_counter.get(group, 0) + 1
                        self._socket_generation_counter[group] = generation
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
                        self._last_raw_frame_at_by_group[group] = now
                        self._last_successful_route_at.pop(group, None)
                        self._last_data_at[group] = now
                        self._last_successful_delivery_at[group] = None
                    try:
                        self._start_socket_connect(
                            socket=socket,
                            group=group,
                            pairs=pairs,
                            subscriptions=subscriptions,
                            generation=generation,
                        )
                    except Exception as exc:
                        # One shard's immediate handshake failure must not prevent
                        # later required shards from getting their first connect attempt.
                        # Keep the subscription intent; the recovery supervisor retries
                        # the failed shard once its old transport has closed.
                        connect_errors.append(exc)
                        app_logger.warning(
                            "Common feed shard connect failed; continuing other required shards group=%s error=%s: %s",
                            f"{group.mode}:{group.shard}", type(exc).__name__, exc,
                        )
                else:
                    # Keep the old acknowledged token set while the broker is
                    # cooling down. Updating it here would lose the pending
                    # delta, so recovery could never apply it after cooldown.
                    if self._broker_cooldown_remaining() > 0.0:
                        continue
                    removed = previous_tokens.get(group, set()) - pairs
                    added = pairs - previous_tokens.get(group, set())
                    if removed:
                        self._run_socket_operation_bounded(
                            socket=socket,
                            group=group,
                            operation=lambda: self._unsubscribe_pairs(socket, group.mode, removed),
                            operation_name="unsubscribe",
                        )
                    if added:
                        self._run_socket_operation_bounded(
                            socket=socket,
                            group=group,
                            operation=lambda: self._subscribe_pairs(socket, group.mode, added),
                            operation_name="subscribe",
                        )
                    with self._lock:
                        self._socket_tokens[group] = set(pairs)

            if connect_errors:
                # Preserve existing error visibility to the caller, but only after
                # every required shard has been attempted.
                raise connect_errors[0]

    @staticmethod
    def _is_broker_connection_limit_error(reason: Any) -> bool:
        text = str(reason).strip().lower()
        return (
            "429" in text
            or "connection limit exceeded" in text
            or "too many connections" in text
            or "maximum connection" in text
        )

    def _apply_broker_cooldown(self, reason: Any) -> bool:
        """Atomically pause new Angel socket connects after a connection-limit error."""
        if not self._is_broker_connection_limit_error(reason):
            return False
        return apply_shared_broker_cooldown(reason, self._broker_cooldown_seconds)

    def _broker_cooldown_remaining(self) -> float:
        return shared_broker_cooldown_remaining()

    def _handle_socket_failure(
        self, group: SocketGroup, socket: Any, generation: int, reason: Any
    ) -> None:
        # Serialize the generation check with invalidation/reconciliation. A late
        # failure callback from a retired socket must not impose a broker cooldown
        # or mark the current authentication session stale.
        with self._reconcile_lock:
            with self._lock:
                is_current = (
                    not self._closed
                    and self._sockets.get(group) is socket
                    and self._socket_generation.get(group) == generation
                )
            if not is_current:
                return
            # Set cooldown before scheduling invalidation/recovery so a current
            # generation's 429 cannot be followed by an immediate reconnect.
            if self._apply_broker_cooldown(reason):
                _mark_socket_auth_stale(socket)
            self._schedule_socket_failure_recovery(group, socket, generation, str(reason))

    def _start_socket_connect(
        self,
        *,
        socket: Any,
        group: SocketGroup,
        pairs: set[tuple[int, str]],
        subscriptions: dict[int, list[str]],
        generation: int,
    ) -> None:
        """Start broker I/O without holding the reconcile lock while it blocks."""
        with self._lock:
            if (
                self._closed
                or self._sockets.get(group) is not socket
                or self._socket_generation.get(group) != generation
            ):
                return
            if self._connect_started_generation.get(group) == generation:
                return
            self._connect_started_generation[group] = generation
            self._connect_pending_generation[group] = generation

        completed = Event()
        failure: dict[str, Exception] = {}

        def _connect() -> None:
            try:
                connect_kwargs = {
                    "mode": group.mode,
                    "subscriptions": subscriptions,
                    "correlation_id": f"common-{group.mode}-{group.shard}",
                    "on_data": lambda message: self._on_data(group, message, generation),
                    "on_failure": lambda reason: self._handle_socket_failure(group, socket, generation, reason),
                }
                if isinstance(socket, MarketDataWebSocket):
                    # The manager owns reconnect policy. Do not let the wrapper
                    # perform several hidden retries before supervisor recovery.
                    connect_kwargs["reconnect_attempts"] = 0
                    connect_kwargs["reconnect_delay_seconds"] = 0.0
                # Keep broker-wide admission through handshake success/failure.
                # MarketDataWebSocket.connect now starts run_forever in a tracked
                # thread and returns once the handshake outcome is known.
                with _SHARED_BROKER_CONNECT_ADMISSION_LOCK:
                    def _still_current() -> bool:
                        with self._lock:
                            return (
                                self._sockets.get(group) is socket
                                and self._socket_generation.get(group) == generation
                                and not self._closed
                            )
                    if not _still_current():
                        self._close_socket_bounded(socket)
                        return
                    # A replacement worker may already be queued for admission
                    # while another shard's close is still in flight. Re-check
                    # retirement after acquiring the global lock, not only when
                    # creating the placeholder socket.
                    while _still_current():
                        with self._lock:
                            retirement_pending = bool(self._retired_transports)
                        if not retirement_pending:
                            break
                        time.sleep(0.05)
                    if not _still_current():
                        self._close_socket_bounded(socket)
                        return
                    if not _wait_for_shared_broker_connect_admission(
                        should_stop=lambda: not _still_current()
                    ):
                        self._close_socket_bounded(socket)
                        return
                    if not _still_current():
                        self._close_socket_bounded(socket)
                        return
                    try:
                        socket.connect(**connect_kwargs)
                    except Exception as connect_exc:
                        # A timed-out/retired connect worker may fail after a
                        # newer generation has replaced it. Only the current
                        # generation is allowed to affect shared broker cooldown
                        # or authentication state.
                        with self._lock:
                            is_current_failure = (
                                self._sockets.get(group) is socket
                                and self._socket_generation.get(group) == generation
                                and not self._closed
                            )
                            if is_current_failure and self._apply_broker_cooldown(connect_exc):
                                _mark_socket_auth_stale(socket)
                        raise
                    # Check and retire a late successful handshake before releasing
                    # global admission; otherwise the next shard could connect while
                    # this just-opened orphan is still closing.
                    with self._lock:
                        is_current_after_connect = (
                            self._sockets.get(group) is socket
                            and self._socket_generation.get(group) == generation
                        )
                    if not is_current_after_connect:
                        close_worker = self._close_socket_tracked(
                            socket, group=group, generation=generation,
                            reason="late_connect_success",
                            on_finished=lambda: self._reconcile_after_retirement(group),
                        )
                        # Fast close workers have no watcher, so their completion
                        # callback will not run. Reconcile explicitly in that path
                        # or the replacement may wait for the periodic supervisor.
                        if not close_worker.is_alive():
                            self._reconcile_after_retirement(group)
                        return
            except Exception as exc:
                failure["exception"] = exc
                # Keep this exception path non-blocking: _start_socket_connect
                # waits briefly for immediate failures, so waiting here for the
                # reconcile lock would hide the original broker error from its
                # caller. The state lock plus generation check protects a newer
                # replacement; the recovery supervisor serializes the rebuild.
                with self._lock:
                    is_current = (
                        self._sockets.get(group) is socket
                        and self._socket_generation.get(group) == generation
                    )
                    if is_current:
                        self._failure_recovery_groups.add(group)
                        self._sockets.pop(group, None)
                        self._socket_tokens.pop(group, None)
                        self._socket_created_at.pop(group, None)
                        self._last_data_at.pop(group, None)
                        self._last_raw_frame_at_by_group.pop(group, None)
                        self._last_successful_route_at.pop(group, None)
                        self._last_successful_delivery_at.pop(group, None)
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
                    context={
                        "event": "socket_connect",
                        "group": f"{group.mode}:{group.shard}",
                        "subscriptions": len(pairs),
                    },
                )
                app_logger.warning(
                    "Common feed socket connect failed group=%s error=%s: %s",
                    group, type(exc).__name__, exc,
                )
                if is_current:
                    def _resume_after_connect_failure(failed_group=group):
                        # This callback can run while _reconcile is waiting for
                        # the connect result; don't acquire its lock or deadlock
                        # the caller that must receive the original exception.
                        with self._lock:
                            self._failure_recovery_groups.discard(failed_group)
                    close_worker = self._close_socket_tracked(
                        socket, group=group, generation=generation, reason="connect_failure",
                        on_finished=_resume_after_connect_failure, timeout=0.1,
                    )
                    if not close_worker.is_alive():
                        _resume_after_connect_failure()
                # Return the connect exception after state and close gating are set,
                # without waiting for any later supervisor reconciliation.
                completed.set()
            finally:
                with self._lock:
                    if self._connect_pending_generation.get(group) == generation:
                        self._connect_pending_generation.pop(group, None)
                completed.set()

        worker = Thread(
            target=_connect,
            name=f"common-ws-connect-{group.mode}-{group.shard}",
            daemon=True,
        )
        worker.start()
        # Keep the old immediate-error behavior for callers/tests, but cap how
        # long reconcile waits. If connect hangs, the recovery loop can replace
        # the socket after _connect_timeout_seconds instead of waiting on this
        # thread while holding _reconcile_lock.
        if completed.wait(timeout=max(0.01, self._connect_call_wait_seconds)):
            error = failure.get("exception")
            if error is not None:
                raise error

    def _schedule_socket_failure_recovery(
        self, group: SocketGroup, socket: Any, generation: int, reason: str
    ) -> None:
        """Invalidate a failed current socket and recover off the SDK callback thread."""
        # A failure callback can arrive while _reconcile() is holding a
        # snapshot of the current sockets. Serialize invalidation with that
        # snapshot so reconcile cannot accidentally keep operating on a failed
        # socket and publish it as current again.
        with self._reconcile_lock:
            with self._lock:
                if self._closed or (
                    self._sockets.get(group) is not socket
                    or self._socket_generation.get(group) != generation
                ):
                    return
                if group in self._failure_recovery_groups:
                    return
                self._failure_recovery_groups.add(group)
                self._sockets.pop(group, None)
                self._socket_tokens.pop(group, None)
                self._socket_created_at.pop(group, None)
                self._last_data_at.pop(group, None)
                self._last_raw_frame_at_by_group.pop(group, None)
                self._last_successful_route_at.pop(group, None)
                self._last_successful_delivery_at.pop(group, None)
                self._socket_generation.pop(group, None)
                self._socket_failure_events += 1
                self._last_socket_failure = {
                    "group": f"{group.mode}:{group.shard}",
                    "reason": str(reason),
                    "failed_at_ns": time.time_ns(),
                    "generation": generation,
                }
        runtime_diagnostics.record(
            component="Common Market Feed",
            error_type="AngelSocketFailure",
            message=str(reason),
            context={"event": "socket_failure_callback", "group": f"{group.mode}:{group.shard}", "generation": generation},
        )
        app_logger.warning(
            "Common feed socket failure callback group=%s generation=%s reason=%s; scheduling immediate recovery",
            group, generation, reason,
        )

        def _recover_after_close() -> None:
            try:
                # Keep admission blocked while the supervisor processes the
                # failure. Clear the marker only immediately before serialized
                # reconciliation, so concurrent reconcile calls cannot race
                # ahead and admit a replacement from stale state.
                self.recover_disconnected(min_age_seconds=0.0, silent_age_seconds=None)
                with self._reconcile_lock:
                    with self._lock:
                        self._failure_recovery_groups.discard(group)
                    self._reconcile()
            except Exception as exc:
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "failure_triggered_recovery", "group": f"{group.mode}:{group.shard}"},
                )
                app_logger.warning(
                    "Common feed failure-triggered recovery failed group=%s; supervisor will retry: %s",
                    group, exc,
                )
                # Only clear a failed generation's marker when no replacement
                # has become current. Never erase a newer generation's failure.
                with self._reconcile_lock:
                    with self._lock:
                        if group not in self._sockets:
                            self._failure_recovery_groups.discard(group)

        def _recover() -> None:
            try:
                close_worker = self._close_socket_tracked(
                    socket, group=group, generation=generation, reason="failure_callback",
                    on_finished=_recover_after_close,
                )
                if close_worker.is_alive():
                    # The tracked close watcher resumes recovery when the transport exits.
                    return
                _recover_after_close()
            except Exception as exc:
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "failure_triggered_recovery", "group": f"{group.mode}:{group.shard}"},
                )
                app_logger.warning(
                    "Common feed failure-triggered recovery failed group=%s; supervisor will retry: %s",
                    group, exc,
                )
                with self._lock:
                    self._failure_recovery_groups.discard(group)

        Thread(target=_recover, name=f"common-ws-failure-recovery-{group.mode}-{group.shard}", daemon=True).start()
    def _run_socket_operation_bounded(
        self,
        *,
        socket: Any,
        group: SocketGroup,
        operation: Callable[[], None],
        operation_name: str,
    ) -> None:
        """Run subscribe/unsubscribe outside the caller's blocking path.

        If the SDK call stalls or fails, invalidate that socket generation and
        keep subscription intent in the registry so the recovery loop can build
        a fresh socket with the complete desired token set.
        """
        completed = Event()
        failure: dict[str, Exception] = {}

        def _run() -> None:
            try:
                operation()
            except Exception as exc:
                failure["exception"] = exc
            finally:
                completed.set()

        worker = Thread(
            target=_run,
            name=f"common-ws-{operation_name}-{group.mode}-{group.shard}",
            daemon=True,
        )
        worker.start()
        finished = completed.wait(timeout=max(0.01, self._socket_call_wait_seconds))
        error = failure.get("exception")
        if finished and error is None:
            return

        # Never leave a potentially half-mutated socket as authoritative.
        with self._lock:
            is_current = self._sockets.get(group) is socket
            if is_current:
                self._sockets.pop(group, None)
                self._socket_tokens.pop(group, None)
                self._socket_created_at.pop(group, None)
                self._last_data_at.pop(group, None)
                self._last_raw_frame_at_by_group.pop(group, None)
                self._last_successful_route_at.pop(group, None)
                self._last_successful_delivery_at.pop(group, None)
                self._socket_generation.pop(group, None)

        if is_current:
            with self._lock:
                self._failure_recovery_groups.add(group)
            def _resume_after_operation_close(failed_group=group):
                # The current reconcile caller owns _reconcile_lock here; only
                # clear the state marker and let the caller unwind on the error.
                with self._lock:
                    self._failure_recovery_groups.discard(failed_group)
            close_worker = self._close_socket_tracked(
                socket, group=group,
                generation=self._socket_generation_counter.get(group),
                reason=f"socket_{operation_name}_failure",
                on_finished=_resume_after_operation_close,
            )
            if not close_worker.is_alive():
                _resume_after_operation_close()

        if not finished:
            error = TimeoutError(
                f"WebSocket {operation_name} call exceeded "
                f"{self._socket_call_wait_seconds:.2f}s for group {group.mode}:{group.shard}"
            )
        assert error is not None
        runtime_diagnostics.record(
            component="Common Market Feed",
            error_type=type(error).__name__,
            message=str(error),
            context={
                "event": f"socket_{operation_name}",
                "group": f"{group.mode}:{group.shard}",
                "timed_out": not finished,
            },
        )
        app_logger.warning(
            "Common feed socket %s failed group=%s error=%s: %s; socket invalidated for recovery",
            operation_name, group, type(error).__name__, error,
        )
        raise error

    def _ensure_recovery_supervisor(self) -> None:
        """Start one process-local self-healing loop for the shared broker feed."""
        with self._lock:
            if self._closed:
                return
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
        # Subscription changes intentionally remain pending during a shared
        # broker cooldown. Reconcile once when that cooldown expires so the
        # remembered socket-token set catches up without sending broker calls
        # while Angel is rate-limiting us.
        cooldown_was_active = False
        while not self._recovery_stop.wait(self._recovery_interval_seconds):
            with self._lock:
                has_intent = bool(self.registry.subscriptions())
            if not has_intent:
                continue
            try:
                # Before/after the regular session, tick silence is expected;
                # don't churn otherwise-connected sockets every 30 seconds.
                # Explicit close/error callbacks and stuck handshakes still recover.
                silent_timeout = (
                    self._silent_feed_timeout_seconds
                    if _is_regular_trading_window()
                    else -1.0
                )
                self.recover_disconnected(
                    min_age_seconds=0.0,
                    silent_age_seconds=silent_timeout,
                )
                cooldown_remaining = self._broker_cooldown_remaining()
                if cooldown_remaining > 0.0:
                    cooldown_was_active = True
                elif cooldown_was_active:
                    self._reconcile()
                    cooldown_was_active = False
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
        """Serialize recovery against reconciliation so concurrent passes cannot race."""
        with self._reconcile_lock:
            with self._lock:
                if self._closed:
                    return 0
            return self._recover_disconnected_serialized(
                min_age_seconds=min_age_seconds,
                silent_age_seconds=silent_age_seconds,
            )

    def _recover_disconnected_serialized(
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
                    # Silence recovery is a transport-liveness decision, so use
                    # raw frame activity rather than successful consumer delivery.
                    # A routing/callback failure must be diagnosed as a data-path
                    # problem, not trigger needless broker reconnects.
                    last_transport_activity_at = self._last_raw_frame_at_by_group.get(
                        group, created_at
                    )
                    if timeout < 0 or now - last_transport_activity_at < float(timeout):
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
            expected_socket_groups: set[SocketGroup] = set()
            for mode, pairs in pairs_by_mode.items():
                for index, _ in enumerate(sorted(pairs)):
                    expected_socket_groups.add(
                        SocketGroup(mode=mode, shard=index // self._max_tokens_per_socket)
                    )
            # Compare identities, not only counts: a stale/unexpected group can
            # make the count look complete while a required shard is missing.
            missing_groups = set(self._sockets) != expected_socket_groups
            # Reconciliation intentionally blocks new broker sessions while any
            # retired transport is still alive. If the only remaining issue is a
            # missing shard, don't count/re-run a recovery attempt on every
            # supervisor tick: the retirement watcher will reconcile when the
            # old transport actually exits.
            retirement_pending = bool(self._retired_transports)

            for group, _, _, _ in stale:
                self._sockets.pop(group, None)
                self._socket_tokens.pop(group, None)
                self._socket_created_at.pop(group, None)
                self._last_data_at.pop(group, None)
                self._last_raw_frame_at_by_group.pop(group, None)
                self._last_successful_route_at.pop(group, None)
                self._last_successful_delivery_at.pop(group, None)
                self._socket_generation.pop(group, None)
        if not stale and not missing_groups:
            return 0
        if not stale and missing_groups and retirement_pending:
            return 0

        with self._lock:
            self._recovery_attempts += 1
            self._last_recovery_at = time.time()

        for stale_group, socket, _, _ in stale:
            self._close_socket_tracked(
                socket, group=stale_group,
                generation=self._socket_generation_counter.get(stale_group),
                reason="stale_recovery",
                on_finished=lambda retired_group=stale_group: self._reconcile_after_retirement(retired_group),
            )
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
        with self._lock:
            # Reject callbacks from an older socket after recovery has replaced
            # or removed that group. Never let an orphan socket inflate telemetry.
            if generation is not None and self._socket_generation.get(group) != generation:
                return
            if group not in self._sockets:
                return
            self._raw_frames_received += 1
            frame_at = time.monotonic()
            self._last_raw_frame_at = frame_at
            self._last_raw_frame_at_by_group[group] = frame_at
            if not isinstance(message, dict):
                self._invalid_frame_types += 1
                return

            token = str(message.get("token") or message.get("symboltoken") or "").strip()
            if not token:
                self._missing_token_frames += 1
                return

            exchange_type_raw = message.get("exchange_type")
            if exchange_type_raw is None:
                exchange_type_raw = message.get("exchangeType")
            exchange_type = None
            if exchange_type_raw is not None:
                try:
                    if isinstance(exchange_type_raw, bool):
                        raise ValueError("exchange_type must be an integer, not bool")
                    numeric_exchange_type = float(exchange_type_raw)
                    if not isfinite(numeric_exchange_type) or not numeric_exchange_type.is_integer():
                        raise ValueError("exchange_type must be a finite integer")
                    exchange_type = int(numeric_exchange_type)
                    if exchange_type <= 0:
                        raise ValueError("exchange_type must be positive")
                except (TypeError, ValueError, OverflowError):
                    self._invalid_exchange_type_frames += 1
                    return

            routes = self._route_index.get(group, {})
            if exchange_type is not None:
                matching = tuple(routes.get((exchange_type, token), ()))
                if not matching and any(route_token == token for _, route_token in routes):
                    self._exchange_mismatch_frames += 1
            else:
                self._missing_exchange_type_frames += 1
                # Token-only fallback must be unambiguous across every shard
                # and exchange group for this subscription mode. Exchange types
                # are partitioned into separate groups, so a local-shard check
                # can silently misroute a duplicate token from another exchange.
                # Other modes are intentionally excluded: the callback's socket
                # group already identifies the subscription mode.
                token_routes = tuple(
                    item
                    for candidate_group, group_routes in self._route_index.items()
                    if candidate_group.mode == group.mode
                    for (route_exchange, route_token), items in group_routes.items()
                    if route_token == token
                    for item in items
                )
                # A token alone is not globally unique across exchanges. Route it
                # only when every matching subscription resolves to one exchange.
                exchanges = {
                    self._resolve_exchange_type(key) for _, key in token_routes
                }
                if len(exchanges) > 1:
                    self._ambiguous_token_frames += 1
                    matching = ()
                else:
                    matching = token_routes
            if not matching:
                self._unmatched_frames += 1
            raw_callbacks = [self._callbacks.get(name) for name, _ in matching]
            record_callbacks = [(self._record_callbacks.get(name), key) for name, key in matching]
            self._missing_callback_routes += sum(
                1 for name, _ in matching
                if self._callbacks.get(name) is None and self._record_callbacks.get(name) is None
            )
            if matching:
                self._ticks_received += 1
                self._last_successful_route_at[group] = time.monotonic()
                routed_exchange_type = (
                    exchange_type
                    if exchange_type is not None
                    else self._resolve_exchange_type(matching[0][1])
                )
                self._ticks_by_exchange[str(routed_exchange_type)] += 1
        for callback in {id(cb): cb for cb in raw_callbacks if cb is not None}.values():
            with self._lock:
                self._raw_callback_attempts += 1
            try:
                callback(message)
                with self._lock:
                    self._raw_callback_successes += 1
                    self._last_data_at[group] = time.monotonic()
                    self._last_successful_delivery_at[group] = self._last_data_at[group]
            except Exception as exc:
                with self._lock:
                    self._delivery_errors += 1
                    self._last_delivery_error = f"{type(exc).__name__}: {exc}"
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
                with self._lock:
                    self._missing_descriptor_records += 1
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type="MissingInstrumentDescriptor",
                    message="Matched WebSocket route has no registry descriptor.",
                    context={"event": "missing_descriptor", "group": f"{group.mode}:{group.shard}", "token": token, "instrument": key.value},
                )
                continue
            try:
                record = self._normalizer.normalize(descriptor, message)
                with self._lock:
                    self._normalized_records_emitted += 1
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
                with self._lock:
                    self._normalized_records_delivered += 1
                    self._last_data_at[group] = time.monotonic()
                    self._last_successful_delivery_at[group] = self._last_data_at[group]
            except (TypeError, ValueError, OverflowError) as exc:
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
                    self._normalized_delivery_errors += 1
                    self._last_delivery_error = f"{type(exc).__name__}: {exc}"
                runtime_diagnostics.record(
                    component="Common Market Feed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context={"event": "normalized_delivery", "group": f"{group.mode}:{group.shard}", "token": token},
                )

    def _watch_retired_transport(
        self,
        close_worker: Thread,
        *,
        group: SocketGroup,
        generation: int | None,
        reason: str,
        on_finished: Callable[[], None] | None = None,
        retirement_key: str | None = None,
    ) -> None:
        """Expose and supervise a retired transport until its close call exits."""
        key = retirement_key or f"{group.mode}:{group.shard}:{generation if generation is not None else 'unknown'}:{reason}:{time.time_ns()}"
        with self._lock:
            self._retired_transports[key] = {
                "group": f"{group.mode}:{group.shard}",
                "generation": generation,
                "reason": reason,
                "retired_at": self._retired_transports.get(key, {}).get("retired_at", time.time()),
                "close_worker_alive": bool(close_worker.is_alive()),
                "last_observed_at": time.time(),
            }

        def _watch() -> None:
            while close_worker.is_alive():
                close_worker.join(timeout=10.0)
                with self._lock:
                    record = self._retired_transports.get(key)
                    if record is not None:
                        record["close_worker_alive"] = bool(close_worker.is_alive())
                        record["last_observed_at"] = time.time()
                if close_worker.is_alive():
                    app_logger.error(
                        "Common feed retired transport still alive group=%s generation=%s reason=%s; replacement remains blocked",
                        f"{group.mode}:{group.shard}", generation, reason,
                    )
            with self._lock:
                self._retired_transports.pop(key, None)
            if on_finished is not None:
                try:
                    on_finished()
                except Exception as exc:
                    app_logger.warning(
                        "Common feed retired transport completion handler failed group=%s reason=%s: %s",
                        f"{group.mode}:{group.shard}", reason, exc,
                    )

        Thread(
            target=_watch,
            name=f"common-ws-retire-watch-{group.mode}-{group.shard}",
            daemon=True,
        ).start()

    def _reconcile_after_retirement(self, group: SocketGroup) -> None:
        """Resume desired-state reconciliation after a retired close completes."""
        try:
            with self._reconcile_lock:
                self._reconcile()
        except Exception as exc:
            app_logger.warning(
                "Common feed reconcile after retired transport close failed group=%s: %s",
                f"{group.mode}:{group.shard}", exc,
            )

    def _close_socket_tracked(
        self,
        socket: Any,
        *,
        group: SocketGroup,
        generation: int | None,
        reason: str,
        on_finished: Callable[[], None] | None = None,
        timeout: float = 2.0,
    ) -> Thread:
        """Register retirement before waiting, so concurrent reconciles see the slot occupied."""
        key = f"{group.mode}:{group.shard}:{generation if generation is not None else 'unknown'}:{reason}:{time.time_ns()}"
        with self._lock:
            self._retired_transports[key] = {
                "group": f"{group.mode}:{group.shard}",
                "generation": generation,
                "reason": reason,
                "retired_at": time.time(),
                "close_worker_alive": True,
                "last_observed_at": time.time(),
            }
        worker = self._close_socket_bounded(
            socket, timeout=timeout, group=group, generation=generation, reason=reason
        )
        if worker.is_alive():
            self._watch_retired_transport(
                worker, group=group, generation=generation, reason=reason,
                on_finished=on_finished, retirement_key=key,
            )
        else:
            with self._lock:
                self._retired_transports.pop(key, None)
        return worker

    @staticmethod
    def _close_socket_bounded(
        socket: Any,
        timeout: float = 2.0,
        *,
        group: SocketGroup | None = None,
        generation: int | None = None,
        reason: str = "lifecycle_cleanup",
    ) -> Thread:
        # Close remains bounded, but logs distinguish manager retirement from
        # broker callbacks and include group/generation context when available.
        app_logger.info(
            "Common feed socket close requested "
            f"group={f'{group.mode}:{group.shard}' if group is not None else 'unknown'} "
            f"generation={generation if generation is not None else 'unknown'} reason={reason}"
        )
        def _close() -> None:
            try:
                socket.close()
            except Exception as exc:
                app_logger.debug(f"Common feed socket close raised: {exc}")
        worker = Thread(target=_close, name="common-ws-close", daemon=True)
        worker.start()
        worker.join(timeout=max(0.1, float(timeout)))
        if worker.is_alive():
            app_logger.error(
                "Common feed socket close timed out "
                f"group={f'{group.mode}:{group.shard}' if group is not None else 'unknown'} "
                f"generation={generation if generation is not None else 'unknown'} reason={reason}"
            )
        return worker

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            sockets = dict(self._sockets)
            # Distinguish the broker session cap from the number of groups the
            # current subscription set actually requires. A partial reconcile can
            # leave a required shard absent from _sockets, so len(sockets) alone
            # must never be used as the health denominator.
            pairs_by_mode: dict[int, set[tuple[int, str]]] = defaultdict(set)
            for sub in self.registry.subscriptions():
                pairs_by_mode[sub.mode].add(
                    (self._resolve_exchange_type(sub.key), sub.key.token.strip())
                )
            required_groups: set[SocketGroup] = set()
            for mode, pairs in pairs_by_mode.items():
                for index, _ in enumerate(sorted(pairs)):
                    required_groups.add(
                        SocketGroup(mode=mode, shard=index // self._max_tokens_per_socket)
                    )
            socket_health_by_group: dict[str, dict[str, Any]] = {}
            for group, socket in sockets.items():
                try:
                    health = getattr(socket, "health", {})
                    if not isinstance(health, dict):
                        health = {}
                except Exception as exc:
                    health = {"health_read_error": f"{type(exc).__name__}: {exc}"}
                # Expose only lifecycle diagnostics, never auth headers, tokens,
                # raw broker frames, or subscription payloads.
                socket_health_by_group[f"{group.mode}:{group.shard}"] = {
                    key: health[key]
                    for key in (
                        "connected",
                        "connecting",
                        "stopping",
                        "error_count",
                        "reconnect_count",
                        "consecutive_failures",
                        "last_error",
                        "transport_thread_alive",
                        "transport_close_timed_out",
                        "initial_subscribe_success",
                        "health_read_error",
                    )
                    if key in health
                }
            return {
                "subscriptions": len(self.registry.subscriptions()),
                "active_instruments": len(self.registry.active_keys()),
                "socket_groups": len(sockets),
                "required_socket_groups": sorted(
                    f"{group.mode}:{group.shard}" for group in required_groups
                ),
                "required_socket_group_count": len(required_groups),
                "missing_socket_groups": sorted(
                    f"{group.mode}:{group.shard}" for group in required_groups if group not in sockets
                ),
                "unconnected_required_groups": sorted(
                    f"{group.mode}:{group.shard}" for group in required_groups
                    if group not in sockets or not bool(getattr(sockets.get(group), "connected", False))
                ),
                "max_socket_sessions": self._max_sockets,
                "consumers": sorted(set(self._callbacks) | set(self._record_callbacks)),
                "socket_health_by_group": socket_health_by_group,
                "delivery_errors": self._delivery_errors,
                "missing_callback_routes": self._missing_callback_routes,
                "missing_descriptor_records": self._missing_descriptor_records,
                "normalized_records_emitted": self._normalized_records_emitted,
                "normalized_records_delivered": self._normalized_records_delivered,
                "raw_callback_attempts": self._raw_callback_attempts,
                "raw_callback_successes": self._raw_callback_successes,
                "missing_exchange_type_frames": self._missing_exchange_type_frames,
                "ambiguous_token_frames": self._ambiguous_token_frames,
                "normalized_delivery_errors": self._normalized_delivery_errors,
                "last_delivery_error": self._last_delivery_error,
                "normalizer_errors": self._normalizer_errors,
                "last_normalizer_error": self._last_normalizer_error,
                "ticks_received": self._ticks_received,
                "raw_frames_received": self._raw_frames_received,
                "invalid_frame_types": self._invalid_frame_types,
                "missing_token_frames": self._missing_token_frames,
                "invalid_exchange_type_frames": self._invalid_exchange_type_frames,
                "unmatched_frames": self._unmatched_frames,
                "exchange_mismatch_frames": self._exchange_mismatch_frames,
                "last_raw_frame_age_seconds": (
                    max(0.0, time.monotonic() - self._last_raw_frame_at)
                    if self._last_raw_frame_at is not None else None
                ),
                "last_raw_frame_age_seconds_by_group": {
                    f"{group.mode}:{group.shard}": max(0.0, time.monotonic() - at)
                    for group, at in self._last_raw_frame_at_by_group.items()
                    if group in sockets
                },
                "last_successful_route_age_seconds_by_group": {
                    f"{group.mode}:{group.shard}": max(0.0, time.monotonic() - at)
                    for group, at in self._last_successful_route_at.items()
                    if group in sockets
                },
                "last_successful_delivery_age_seconds_by_group": {
                    f"{group.mode}:{group.shard}": (
                        None if delivered_at is None
                        else max(0.0, time.monotonic() - delivered_at)
                    )
                    for group, delivered_at in self._last_successful_delivery_at.items()
                    if group in sockets
                },
                "runtime_started_at": self._runtime_started_at,
                "ticks_by_exchange_type": dict(self._ticks_by_exchange),
                "last_tick": dict(self._last_tick) if self._last_tick else None,
                "connected_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if bool(getattr(socket, "connected", False))
                ],
                "transport_threads_alive_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if bool(
                        getattr(socket, "health", {}).get("transport_thread_alive", False)
                        if isinstance(getattr(socket, "health", {}), dict) else False
                    )
                ],
                "transport_close_timed_out_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if bool(
                        getattr(socket, "health", {}).get("transport_close_timed_out", False)
                        if isinstance(getattr(socket, "health", {}), dict) else False
                    )
                ],
                "disconnected_groups": [
                    f"{group.mode}:{group.shard}"
                    for group, socket in sockets.items()
                    if not bool(getattr(socket, "connected", False))
                ],
                "connect_failures": self._connect_failures,
                "broker_cooldown_remaining_seconds": self._broker_cooldown_remaining(),
                "last_connect_failure": dict(self._last_connect_failure) if self._last_connect_failure else None,
                "socket_failure_events": self._socket_failure_events,
                "last_socket_failure": dict(self._last_socket_failure) if self._last_socket_failure else None,
                "failure_recovery_groups": sorted(
                    f"{group.mode}:{group.shard}" for group in self._failure_recovery_groups
                ),
                "retired_transport_count": len(self._retired_transports),
                "retired_transports": [dict(item) for item in self._retired_transports.values()],
                "retired_transport_groups": sorted({
                    str(item.get("group")) for item in self._retired_transports.values()
                }),
                "recovery_attempts": self._recovery_attempts,
                "last_recovery_at": self._last_recovery_at,
            }

    def close(self) -> None:
        self._recovery_stop.set()
        # Serialize shutdown with reconciliation/recovery so a late pass cannot
        # publish fresh sockets after shutdown clears the active sessions.
        with self._reconcile_lock:
            with self._lock:
                self._closed = True
                recovery_thread = self._recovery_thread
                self._recovery_thread = None
                sockets = list(self._sockets.values())
                self._sockets.clear()
                self._socket_tokens.clear()
                self._socket_created_at.clear()
                self._last_raw_frame_at_by_group.clear()
                self._last_successful_route_at.clear()
                self._last_data_at.clear()
                self._last_successful_delivery_at.clear()
                self._socket_generation.clear()
                self._failure_recovery_groups.clear()
            for socket in sockets:
                self._close_socket_bounded(socket, reason="manager_shutdown")
        if recovery_thread is not None and recovery_thread is not current_thread():
            recovery_thread.join(timeout=1.0)
