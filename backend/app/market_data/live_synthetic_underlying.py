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

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})


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
        concrete_tokens: dict[str, str] | None = None,
        index_symbols: frozenset[str] = frozenset(),
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
        self.concrete_tokens = {str(k).strip().upper(): str(v).strip() for k, v in (concrete_tokens or {}).items() if str(k).strip() and str(v).strip()}
        self.index_symbols = frozenset(str(symbol).strip().upper() for symbol in index_symbols if str(symbol).strip())
        self.stop_event = Event()
        self._socket: MarketDataWebSocket | None = None
        self._sockets: list[MarketDataWebSocket] = []

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
            token = self.concrete_tokens.get(symbol)
            if token:
                result[symbol] = token
                continue
            if symbol in self.index_symbols:
                exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
                result[symbol] = self.instrument_master.resolve_index_token(symbol, exchange)
            else:
                instrument = self.instrument_master.resolve_cash_instrument(symbol, "NSE")
                result[symbol] = str(instrument["token"])
        return result

    def _exchange_type(self, symbol: str) -> int:
        return 4 if symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols else 1

    def _subscription_groups(self, tokens: dict[str, str]) -> dict[int, list[str]]:
        groups: dict[int, list[str]] = {}
        for symbol, token in tokens.items():
            groups.setdefault(self._exchange_type(symbol), []).append(token)
        return groups

    def run_forever(self) -> None:
        tokens = self._tokens()
        token_to_symbol = {token: symbol for symbol, token in tokens.items()}
        queue: Queue[dict[str, Any]] = Queue()
        self.auth.login()
        sockets: list[MarketDataWebSocket] = []
        self._sockets = sockets
        self._socket = None

        def receive(message: Any) -> None:
            if isinstance(message, dict):
                queue.put(message)

        for exchange_type, grouped_tokens in sorted(self._subscription_groups(tokens).items()):
            socket = MarketDataWebSocket(auth=self.auth)
            sockets.append(socket)
            if self._socket is None:
                self._socket = socket
            Thread(
                target=socket.connect,
                kwargs={
                    "exchange_type": exchange_type,
                    "tokens": list(dict.fromkeys(grouped_tokens)),
                    "mode": 3,
                    "correlation_id": f"synthetic-atm-underlyings-{exchange_type}",
                    "on_data": receive,
                    "reconnect_attempts": 3,
                    "reconnect_delay_seconds": 2,
                },
                daemon=True,
                name=f"synthetic-atm-underlyings-{exchange_type}",
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
        for socket in self._sockets:
            socket.close()
        self._sockets = []
        self._socket = None


__all__ = ["LiveSyntheticUnderlyingFeed"]
