"""Shared Index Runner's sole adapter to the production Angel One WebSocket.

The runner owns the broker subscription lifecycle and publishes canonical
MarketDataRecord values. Strategy code must consume runner state, not attach
additional consumers to the production manager.
"""
from __future__ import annotations

from threading import Lock, RLock
from time import time_ns
from typing import Any, Callable, Iterable

from app.algo.auth import AngelOneAuth
from app.market_data.websocket import MarketDataWebSocket

from .common_websocket import CommonWebSocketManager
from .contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType
from .registry import InstrumentDescriptor
from .shared_cache import get_shared_market_data_cache

_EXCHANGE_SEGMENTS = {
    "NSE": "NSE", "NFO": "NFO", "BSE": "BSE",
    "BFO": "BFO", "MCX": "MCX", "NCDEX": "NCDEX",
}
_SHARED_LOCK = Lock()
_SHARED_MANAGER: CommonWebSocketManager | None = None
_SHARED_AUTH: AngelOneAuth | None = None
_SHARED_MANAGER_CLOSING = False


class SharedIndexOnlyWebSocketManager(CommonWebSocketManager):
    """Production broker manager accepts subscriptions only from the shared index runner."""

    ALLOWED_CONSUMERS = frozenset({
        "shared-index-market-data:spot",
        "shared-index-market-data:options",
        "shared-index-market-data:futures",
    })

    @classmethod
    def validate_consumer(cls, consumer: str) -> str:
        normalized = str(consumer or "").strip()
        if normalized not in cls.ALLOWED_CONSUMERS:
            raise PermissionError(
                "the production Angel One WebSocket is owned exclusively by "
                "SharedIndexMarketDataRunner (NIFTY/BANKNIFTY)"
            )
        return normalized

    @staticmethod
    def validate_descriptor(descriptor: InstrumentDescriptor) -> None:
        underlying = str(getattr(descriptor, "underlying", "") or "").strip().upper()
        kind = str(getattr(descriptor, "instrument_type", "") or "").strip().lower()
        exchange = str(getattr(descriptor, "exchange", "") or "").strip().upper()
        segment = str(getattr(descriptor, "segment", "") or "").strip().upper()
        key_exchange = str(getattr(descriptor.key, "exchange", "") or "").strip().upper()
        key_segment = str(getattr(descriptor.key, "segment", "") or "").strip().upper()
        expected_exchange = "NSE" if kind == "index" else "NFO"
        if underlying not in {"NIFTY", "BANKNIFTY"}:
            raise PermissionError("production live universe is locked to NIFTY/BANKNIFTY")
        if kind not in {"index", "option", "future"}:
            raise PermissionError("production live feed accepts only index, option and future contracts")
        if (exchange, segment, key_exchange, key_segment) != (
            expected_exchange, expected_exchange, expected_exchange, expected_exchange
        ):
            raise PermissionError("production live feed rejects instruments outside NSE/NFO index contracts")
        if kind == "index" and str(descriptor.symbol).strip().upper() != underlying:
            raise PermissionError("spot descriptor symbol must match its locked underlying")
        if kind == "option":
            required_expiry_type = "WEEKLY" if underlying == "NIFTY" else "MONTHLY"
            if str(descriptor.expiry_type or "").strip().upper() != required_expiry_type:
                raise PermissionError(
                    f"{underlying} options are restricted to {required_expiry_type} expiry"
                )

    def register_callback(self, consumer: str, callback) -> None:
        self.validate_consumer(consumer)
        return super().register_callback(consumer, callback)

    def unregister_callback(self, consumer: str) -> None:
        self.validate_consumer(consumer)
        return super().unregister_callback(consumer)

    def register_normalized_callback(self, consumer: str, callback) -> None:
        self.validate_consumer(consumer)
        return super().register_normalized_callback(consumer, callback)

    def unregister_normalized_callback(self, consumer: str) -> None:
        self.validate_consumer(consumer)
        return super().unregister_normalized_callback(consumer)

    def register_normalized_observer(self, consumer: str, callback) -> None:
        self.validate_consumer(consumer)
        return super().register_normalized_observer(consumer, callback)

    def unregister_normalized_observer(self, consumer: str) -> None:
        self.validate_consumer(consumer)
        return super().unregister_normalized_observer(consumer)

    def register_transport_observer(self, consumer: str, callback) -> None:
        self.validate_consumer(consumer)
        return super().register_transport_observer(consumer, callback)

    def unregister_transport_observer(self, consumer: str) -> None:
        self.validate_consumer(consumer)
        return super().unregister_transport_observer(consumer)

    def subscribe(self, consumer: str, keys, mode: int = 1):
        self.validate_consumer(consumer)
        for key in keys:
            descriptor = self.registry.get(key)
            if descriptor is None:
                raise KeyError(f"instrument is not registered: {key.value}")
            self.validate_descriptor(descriptor)
        return super().subscribe(consumer, keys, mode)

    def unsubscribe(self, consumer: str, keys=None) -> None:
        self.validate_consumer(consumer)
        return super().unsubscribe(consumer, keys)

    def clear_consumer(self, consumer: str) -> None:
        self.validate_consumer(consumer)
        return super().clear_consumer(consumer)


def shared_common_manager_snapshot() -> dict[str, Any] | None:
    """Return runtime state without creating the shared manager or persisting ticks."""
    with _SHARED_LOCK:
        manager = _SHARED_MANAGER
    if manager is None:
        return None
    health_snapshot = getattr(manager, "health_snapshot", None)
    return health_snapshot() if callable(health_snapshot) else manager.snapshot()


def shared_common_manager(
    auth: AngelOneAuth | None = None,
    socket_factory: Callable[[], Any] | None = None,
) -> CommonWebSocketManager:
    """Return the process-wide manager reserved for SharedIndexMarketDataRunner."""
    global _SHARED_MANAGER, _SHARED_AUTH
    with _SHARED_LOCK:
        if _SHARED_MANAGER_CLOSING:
            raise RuntimeError("shared market-data manager is shutting down")
        if _SHARED_MANAGER is not None and bool(getattr(_SHARED_MANAGER, "_closed", False)):
            raise RuntimeError(
                "previous shared market-data manager is closed but not fully retired; "
                "retry shutdown before requesting a replacement"
            )
        if _SHARED_MANAGER is None:
            _SHARED_AUTH = auth or AngelOneAuth()
            factory = socket_factory or (lambda: MarketDataWebSocket(auth=_SHARED_AUTH, auto_reconnect=False))
            _SHARED_MANAGER = SharedIndexOnlyWebSocketManager(
                socket_factory=factory,
                # Keep the broker transport callback limited to bounded enqueue.
                # Strategy normalization/cache fan-out runs on the shard worker,
                # so a slow consumer cannot stall Angel's ping/pong loop.
                async_callback_dispatch=True,
            )
        return _SHARED_MANAGER


def close_shared_common_manager() -> None:
    """Close the process-owned manager after every feed consumer has stopped.

    The closing fence is published under the singleton lock, but manager.close()
    runs outside that lock because transport callbacks and retirement workers
    may still need manager-local synchronization while they drain.
    """
    global _SHARED_MANAGER, _SHARED_AUTH, _SHARED_MANAGER_CLOSING
    with _SHARED_LOCK:
        if _SHARED_MANAGER_CLOSING:
            return
        manager = _SHARED_MANAGER
        if manager is None:
            _SHARED_AUTH = None
            return
        _SHARED_MANAGER_CLOSING = True
    try:
        manager.close()
        # close() is deliberately bounded and may return while a socket-close
        # worker or its watcher is still alive. Do not release the process-wide
        # singleton in that state: a later shared_common_manager() call could
        # otherwise create a second broker session beside the unretired one.
        retirement = manager.snapshot()
        unresolved = {
            "retired_transport_count": int(retirement.get("retired_transport_count", 0) or 0),
            "retired_transport_close_start_pending_count": int(
                retirement.get("retired_transport_close_start_pending_count", 0) or 0
            ),
            "retired_transport_watch_pending_count": int(
                retirement.get("retired_transport_watch_pending_count", 0) or 0
            ),
        }
        if any(unresolved.values()):
            raise RuntimeError(
                "shared market-data manager shutdown is incomplete; "
                f"retired transport state remains: {unresolved}"
            )
    except Exception:
        # Keep the failed manager reachable for diagnostics/retry. Clearing it
        # here could allow a second broker manager while retired transports
        # from the first one are still alive.
        with _SHARED_LOCK:
            _SHARED_MANAGER_CLOSING = False
        raise
    else:
        with _SHARED_LOCK:
            if _SHARED_MANAGER is manager:
                _SHARED_MANAGER = None
                _SHARED_AUTH = None
            _SHARED_MANAGER_CLOSING = False


def _record_from_snapshot(descriptor: InstrumentDescriptor, snapshot: dict[str, Any]) -> MarketDataRecord | None:
    timestamp_ns = snapshot.get("timestamp_ns")
    if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < 0:
        return None
    try:
        instrument_type = InstrumentType(str(descriptor.instrument_type).strip().lower())
        raw_option_type = descriptor.option_type or snapshot.get("option_type")
        option_type = OptionType(str(raw_option_type).strip().upper()) if raw_option_type else None
        if instrument_type is InstrumentType.OPTION and option_type is None:
            return None
        raw_payload = snapshot.get("payload")
        return MarketDataRecord(
            instrument=descriptor.key,
            symbol=descriptor.symbol,
            instrument_type=instrument_type,
            timestamp_ns=timestamp_ns,
            exchange_timestamp_ns=(
                snapshot.get("exchange_timestamp_ns")
                if isinstance(snapshot.get("exchange_timestamp_ns"), int)
                and not isinstance(snapshot.get("exchange_timestamp_ns"), bool)
                and snapshot.get("exchange_timestamp_ns") >= 0
                else None
            ),
            received_at_monotonic_ns=(
                snapshot.get("received_at_monotonic_ns")
                if isinstance(snapshot.get("received_at_monotonic_ns"), int)
                and not isinstance(snapshot.get("received_at_monotonic_ns"), bool)
                and snapshot.get("received_at_monotonic_ns") >= 0
                else None
            ),
            timeframe=str(snapshot.get("timeframe") or "1s"),
            ltp=snapshot.get("ltp"),
            bid=snapshot.get("bid"),
            ask=snapshot.get("ask"),
            bid_qty=snapshot.get("bid_qty"),
            ask_qty=snapshot.get("ask_qty"),
            volume=snapshot.get("volume"),
            oi=snapshot.get("oi"),
            open=snapshot.get("open"),
            high=snapshot.get("high"),
            low=snapshot.get("low"),
            close=snapshot.get("close"),
            underlying=descriptor.underlying or snapshot.get("underlying"),
            expiry=descriptor.expiry or snapshot.get("expiry"),
            strike=descriptor.strike if descriptor.strike is not None else snapshot.get("strike"),
            option_type=option_type,
            lot_size=descriptor.lot_size if descriptor.lot_size is not None else snapshot.get("lot_size"),
            tick_size=descriptor.tick_size if descriptor.tick_size is not None else snapshot.get("tick_size"),
            payload=dict(raw_payload) if isinstance(raw_payload, dict) else {},
        )
    except (TypeError, ValueError):
        return None


def replay_cached_snapshots(
    descriptors: Iterable[InstrumentDescriptor],
    callback: Callable[[MarketDataRecord], None],
    *,
    max_age_seconds: float = 5.0,
    max_future_skew_seconds: float = 1.0,
) -> int:
    """Replay only exchange-timestamp-fresh snapshots into an opted-in consumer."""
    if max_age_seconds <= 0 or max_future_skew_seconds < 0:
        raise ValueError("snapshot freshness bounds are invalid")
    now_ns = time_ns()
    max_age_ns = int(max_age_seconds * 1_000_000_000)
    max_future_ns = int(max_future_skew_seconds * 1_000_000_000)
    cache = get_shared_market_data_cache()
    replayed = 0
    for descriptor in descriptors:
        snapshot = cache.get(descriptor.key.value)
        if snapshot is None:
            continue
        record = _record_from_snapshot(descriptor, snapshot)
        if record is None:
            continue
        age_ns = now_ns - record.timestamp_ns
        if age_ns > max_age_ns or age_ns < -max_future_ns:
            continue
        callback(record)
        replayed += 1
    return replayed


class CommonStrategyMarketFeed:
    """Own one consumer registration on the process-wide common feed."""

    def __init__(
        self,
        consumer: str,
        *,
        auth: AngelOneAuth | None = None,
        manager: CommonWebSocketManager | None = None,
        socket_factory: Callable[[], Any] | None = None,
        mode: int = 3,
        warm_start: bool = False,
    ) -> None:
        self.consumer = str(consumer).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        self.mode = mode
        self.warm_start = bool(warm_start)
        self.manager = manager or shared_common_manager(auth, socket_factory=socket_factory)
        self._started = False
        self._last_timestamp_ns: dict[str, int] = {}
        self._timestamp_guard = Lock()
        self._callback_locks: dict[str, Lock] = {}
        # Each start/stop creates a callback epoch. Retired closures may still
        # be invoked by a queued transport frame, but must not cross epochs.
        self._callback_epoch = 0
        self._callback_epoch_lock = RLock()

    @staticmethod
    def descriptor(
        *,
        exchange: str,
        token: str,
        symbol: str,
        instrument_type: str,
        segment: str | None = None,
        expiry: str | None = None,
        strike: float | None = None,
        option_type: str | None = None,
        lot_size: int | None = None,
        tick_size: float | None = None,
        underlying: str | None = None,
        expiry_type: str | None = None,
    ) -> InstrumentDescriptor:
        exchange = str(exchange).strip().upper()
        if exchange not in _EXCHANGE_SEGMENTS:
            raise ValueError(f"unsupported exchange: {exchange}")
        key = InstrumentKey(exchange, segment or _EXCHANGE_SEGMENTS[exchange], str(token).strip())
        return InstrumentDescriptor(
            key=key,
            symbol=str(symbol).strip(),
            instrument_type=str(instrument_type).strip().lower(),
            exchange=exchange,
            segment=key.segment,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            lot_size=lot_size,
            tick_size=tick_size,
            underlying=(str(underlying).strip().upper() or None) if underlying is not None else None,
            expiry_type=(str(expiry_type).strip().upper() or None) if expiry_type is not None else None,
        )

    def start(
        self,
        descriptors: Iterable[InstrumentDescriptor],
        callback: Callable[[Any], None],
    ) -> tuple[InstrumentKey, ...]:
        descriptors = tuple(descriptors)
        if not descriptors:
            raise ValueError("at least one descriptor is required")
        if not callable(callback):
            raise TypeError("callback must be callable")
        if len({descriptor.key for descriptor in descriptors}) != len(descriptors):
            raise ValueError("duplicate instruments in feed start")
        validate_consumer = getattr(self.manager, "validate_consumer", None)
        if callable(validate_consumer):
            validate_consumer(self.consumer)
        validate_descriptor = getattr(self.manager, "validate_descriptor", None)
        if callable(validate_descriptor):
            for descriptor in descriptors:
                validate_descriptor(descriptor)
        # InstrumentKey is the broker-level identity. Multiple strategy
        # consumers may describe the same broker instrument with different
        # strategy metadata (for example an option subscription carrying its
        # own strike/expiry). Reuse the already-registered canonical
        # descriptor instead of treating that shared identity as a conflict.
        fresh = []
        for descriptor in descriptors:
            existing = self.manager.registry.get(descriptor.key)
            if existing is None:
                fresh.append(descriptor)
        if fresh:
            self.manager.registry.register_many(tuple(fresh))
        with self._callback_epoch_lock:
            self._callback_epoch += 1
            callback_epoch = self._callback_epoch
            self._last_timestamp_ns.clear()

        def ordered_callback(record: MarketDataRecord) -> None:
            key = record.instrument.value
            with self._timestamp_guard:
                callback_lock = self._callback_locks.setdefault(key, Lock())
            with callback_lock:
                # The epoch lock spans consumer delivery so stop() is a barrier:
                # once it returns, an old callback cannot run or leak into a
                # subsequent start with the same consumer identity.
                with self._callback_epoch_lock:
                    if callback_epoch != self._callback_epoch:
                        return
                    previous = self._last_timestamp_ns.get(key)
                    if previous is not None and record.timestamp_ns < previous:
                        return
                    self._last_timestamp_ns[key] = record.timestamp_ns
                    callback(record)

        self.manager.register_normalized_callback(self.consumer, ordered_callback)
        if self.warm_start:
            replay_cached_snapshots(descriptors, ordered_callback)
        keys = tuple(d.key for d in descriptors)
        self.manager.subscribe(self.consumer, list(keys), mode=self.mode)
        self._started = True
        return keys

    def stop(self) -> None:
        with self._callback_epoch_lock:
            if not self._started:
                return
            # Invalidate queued callbacks before mutating shared manager state.
            self._callback_epoch += 1
            self._started = False
        self.manager.clear_consumer(self.consumer)

    def snapshot(self) -> dict[str, Any]:
        return self.manager.snapshot()

    def close(self) -> None:
        self.stop()


__all__ = ["CommonStrategyMarketFeed", "replay_cached_snapshots", "shared_common_manager", "close_shared_common_manager"]
