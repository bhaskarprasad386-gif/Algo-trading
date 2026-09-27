"""Live underlying-price feed for source-backed synthetic ATM tracking."""

from __future__ import annotations

from queue import Queue, Empty
from threading import Event, Thread
from time import sleep
from typing import Any, Callable

from app.algo.auth import AngelOneAuth
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.market_data.websocket import MarketDataWebSocket


class LiveSyntheticUnderlyingFeed:
    """Subscribe to real NSE underlying tokens and update an ATM tracker."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        tracker: LiveSyntheticAtmTracker,
        instrument_master: InstrumentMaster | None = None,
        auth: AngelOneAuth | None = None,
        on_price: Callable[[str, float, int | None], None] | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("at least one underlying symbol is required")
        self.symbols = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not self.symbols:
            raise ValueError("underlying symbols must be non-empty")
        self.tracker = tracker
        self.instrument_master = instrument_master or InstrumentMaster()
        self.auth = auth or AngelOneAuth()
        self.on_price = on_price
        self.stop_event = Event()
        self._socket: MarketDataWebSocket | None = None

    @staticmethod
    def _price(message: dict[str, Any]) -> float | None:
        try:
            value = float(message.get("last_traded_price"))
        except (TypeError, ValueError):
            return None
        return value / 100.0 if value > 0 else None

    @staticmethod
    def _timestamp_ns(message: dict[str, Any]) -> int | None:
        try:
            value = int(float(message.get("exchange_timestamp")))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        if value < 10_000_000_000:
            return value * 1_000_000_000
        if value < 10_000_000_000_000:
            return value * 1_000_000
        return value * 1_000

    def _tokens(self) -> dict[str, str]:
        self.instrument_master.download()
        result: dict[str, str] = {}
        for symbol in self.symbols:
            instrument = self.instrument_master.resolve_cash_instrument(symbol, "NSE")
            result[symbol] = str(instrument["token"])
        return result

    def run_forever(self) -> None:
        token_to_symbol = {token: symbol for symbol, token in self._tokens().items()}
        queue: Queue[dict[str, Any]] = Queue()
        self.auth.login()
        socket = MarketDataWebSocket(auth=self.auth)
        self._socket = socket

        def receive(message: Any) -> None:
            if isinstance(message, dict):
                queue.put(message)

        Thread(
            target=socket.connect,
            kwargs={
                "exchange_type": 1,
                "tokens": list(token_to_symbol),
                "mode": 3,
                "correlation_id": "synthetic-atm-underlyings",
                "on_data": receive,
                "reconnect_attempts": 3,
                "reconnect_delay_seconds": 2,
            },
            daemon=True,
            name="synthetic-atm-underlyings",
        ).start()

        while not self.stop_event.is_set():
            try:
                message = queue.get(timeout=1.0)
            except Empty:
                continue
            symbol = token_to_symbol.get(str(message.get("token") or "").strip())
            price = self._price(message)
            if symbol is None or price is None:
                continue
            timestamp_ns = self._timestamp_ns(message)
            self.tracker.update(symbol, price)
            if self.on_price is not None:
                self.on_price(symbol, price, timestamp_ns)

    def stop(self) -> None:
        self.stop_event.set()
        if self._socket is not None:
            self._socket.close()
            self._socket = None


__all__ = ["LiveSyntheticUnderlyingFeed"]
