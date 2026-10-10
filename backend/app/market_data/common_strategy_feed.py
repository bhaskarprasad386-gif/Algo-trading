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
from .contracts import InstrumentKey
from .registry import InstrumentDescriptor

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
        self.manager.register_normalized_callback(self.consumer, callback)
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


__all__ = ["CommonStrategyMarketFeed", "shared_common_manager"]
