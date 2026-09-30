"""Strategy-facing adapter over the shared Angel One WebSocket manager.

Strategies register concrete instruments and consume canonical MarketDataRecord
values. No strategy opens or owns a broker WebSocket directly.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from app.algo.auth import AngelOneAuth

from .common_websocket import CommonWebSocketManager
from .contracts import InstrumentKey
from .registry import InstrumentDescriptor


_EXCHANGE_SEGMENTS = {
    "NSE": "NSE",
    "NFO": "NFO",
    "BSE": "BSE",
    "BFO": "BFO",
    "MCX": "MCX",
    "NCDEX": "NCDEX",
}


class CommonStrategyMarketFeed:
    """Own one consumer registration on the process-wide common feed."""

    def __init__(
        self,
        consumer: str,
        *,
        auth: AngelOneAuth | None = None,
        manager: CommonWebSocketManager | None = None,
        mode: int = 3,
    ) -> None:
        self.consumer = str(consumer).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        if mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        self.mode = mode
        self.auth = auth or AngelOneAuth()
        self.manager = manager or CommonWebSocketManager(
            socket_factory=lambda: __import__(
                "app.market_data.websocket", fromlist=["MarketDataWebSocket"]
            ).MarketDataWebSocket(auth=self.auth)
        )
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
        self.manager.registry.register_many(descriptors)
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
        self.manager.close()


__all__ = ["CommonStrategyMarketFeed"]
