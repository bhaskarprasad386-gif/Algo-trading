"""Strategy-facing adapter over the shared Angel One WebSocket manager.

Strategies register concrete instruments and consume canonical MarketDataRecord
values. No strategy opens or owns a broker WebSocket directly.
"""
from __future__ import annotations

from threading import Lock
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


def shared_common_manager_snapshot() -> dict[str, Any] | None:
    """Return runtime state without creating the shared manager or persisting ticks."""
    with _SHARED_LOCK:
        manager = _SHARED_MANAGER
    return None if manager is None else manager.snapshot()


def shared_common_manager(
    auth: AngelOneAuth | None = None,
    socket_factory: Callable[[], Any] | None = None,
) -> CommonWebSocketManager:
    """Return the process-wide common broker manager used by strategy consumers."""
    global _SHARED_MANAGER, _SHARED_AUTH
    with _SHARED_LOCK:
        if _SHARED_MANAGER is None:
            _SHARED_AUTH = auth or AngelOneAuth()
            factory = socket_factory or (lambda: MarketDataWebSocket(auth=_SHARED_AUTH, auto_reconnect=False))
            _SHARED_MANAGER = CommonWebSocketManager(socket_factory=factory)
        return _SHARED_MANAGER


def _record_from_snapshot(
    descriptor: InstrumentDescriptor,
    snapshot: dict[str, Any],
) -> MarketDataRecord | None:
    """Rebuild a canonical record from a still-live shared-cache snapshot."""
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
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        return MarketDataRecord(
            instrument=descriptor.key,
            symbol=descriptor.symbol,
            instrument_type=instrument_type,
            timestamp_ns=timestamp_ns,
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
            payload=payload,
        )
    except (TypeError, ValueError):
        # A cache entry written by a different/older producer is not allowed
        # to break live subscription startup.
        return None


def replay_cached_snapshots(
    manager: CommonWebSocketManager,
    descriptors: Iterable[InstrumentDescriptor],
    callback: Callable[[MarketDataRecord], None],
) -> int:
    """Seed a consumer from valid, unexpired snapshots before live subscription."""
    cache = get_shared_market_data_cache()
    replayed = 0
    for descriptor in descriptors:
        snapshot = cache.get(descriptor.key.value)
        if snapshot is None:
            continue
        record = _record_from_snapshot(descriptor, snapshot)
        if record is None:
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
    ) -> None:
        self.consumer = str(consumer).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        self.mode = mode
        self.manager = manager or shared_common_manager(auth, socket_factory=socket_factory)
        self._started = False
        self._last_timestamp_ns: dict[str, int] = {}

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
        self._last_timestamp_ns.clear()

        def ordered_callback(record: MarketDataRecord) -> None:
            key = record.instrument.value
            previous = self._last_timestamp_ns.get(key)
            if previous is not None and record.timestamp_ns <= previous:
                return
            self._last_timestamp_ns[key] = record.timestamp_ns
            callback(record)

        self.manager.register_normalized_callback(self.consumer, ordered_callback)
        # Warm-start local scanner state from the latest live snapshot before
        # registering broker subscriptions. The timestamp gate above prevents a
        # concurrently arriving live tick from being overwritten by an older seed.
        replay_cached_snapshots(self.manager, descriptors, ordered_callback)
        keys = tuple(d.key for d in descriptors)
        self.manager.subscribe(self.consumer, list(keys), mode=self.mode)
        self._started = True
        return keys

    def stop(self) -> None:
        if not self._started:
            return
        self.manager.clear_consumer(self.consumer)
        self._started = False

    def snapshot(self) -> dict[str, Any]:
        return self.manager.snapshot()

    def close(self) -> None:
        self.stop()


__all__ = ["CommonStrategyMarketFeed", "replay_cached_snapshots", "shared_common_manager"]
