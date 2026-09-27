"""Continuous option+future live recorder for synthetic arbitrage.

The collector is deliberately broker-neutral at the normalization boundary:
Angel One WebSocket ticks are converted to source-backed records and persisted
through the existing bounded LiveMarketDataRecorder. It does not fabricate
quotes, strikes, fills, or P&L, and live orders are never placed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic, sleep, time_ns
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.backtesting.historical_catalog import HistoricalCatalog
from app.market_data.live_recorder import LiveMarketDataRecorder
from app.market_data.websocket import MarketDataWebSocket

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)


@dataclass(frozen=True)
class SyntheticSubscription:
    """One concrete Angel One contract to record."""

    exchange_type: int
    token: str
    symbol: str
    underlying: str
    instrument_class: str
    expiry: str | None = None
    option_type: str | None = None
    strike: float | None = None
    lot_size: int | None = None


class LiveSyntheticOptionFutureRecorder:
    """Persist live option/future ticks with bounded memory.

    The caller supplies concrete contracts from the instrument master. This is
    intentional: the collector never invents strikes or silently substitutes
    a contract when an instrument is missing.
    """

    SOURCE = "angelone-live-synthetic"

    def __init__(
        self,
        data_db: str,
        subscriptions: list[SyntheticSubscription],
        *,
        auth: AngelOneAuth | None = None,
        batch_size: int = 256,
        poll_seconds: float = 0.25,
        on_observation: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not subscriptions:
            raise ValueError("at least one synthetic subscription is required")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        normalized: list[SyntheticSubscription] = []
        seen: set[tuple[int, str]] = set()
        for item in subscriptions:
            token = str(item.token).strip()
            symbol = str(item.symbol).strip()
            underlying = str(item.underlying).strip().upper()
            cls = str(item.instrument_class).strip().upper()
            if not token or not symbol or not underlying:
                raise ValueError("subscription token, symbol and underlying are required")
            if cls not in {"STOCK", "INDEX"}:
                raise ValueError("instrument_class must be STOCK or INDEX")
            key = (int(item.exchange_type), token)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                SyntheticSubscription(
                    exchange_type=int(item.exchange_type),
                    token=token,
                    symbol=symbol,
                    underlying=underlying,
                    instrument_class=cls,
                    expiry=item.expiry,
                    option_type=item.option_type,
                    strike=item.strike,
                    lot_size=item.lot_size,
                )
            )
        self.data_db = data_db
        self.subscriptions = tuple(normalized)
        self.auth = auth or AngelOneAuth()
        self.batch_size = batch_size
        self.poll_seconds = poll_seconds
        self.on_observation = on_observation
        self.stop_event = Event()
        self._sockets: list[MarketDataWebSocket] = []

    @staticmethod
    def market_open(now: datetime | None = None) -> bool:
        current = now or datetime.now(IST)
        return current.weekday() < 5 and OPEN <= current.time() <= CLOSE

    @staticmethod
    def _ltp(message: dict[str, Any]) -> float | None:
        value = message.get("last_traded_price")
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price / 100.0

    @staticmethod
    def _side(message: dict[str, Any], key: str) -> tuple[float | None, float | None]:
        levels = message.get(key)
        if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
            return None, None
        level = levels[0]
        try:
            price = float(level.get("price")) / 100.0
        except (TypeError, ValueError):
            price = 0.0
        try:
            quantity = float(level.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0.0
        return (price if price > 0 else None, quantity if quantity > 0 else None)

    def _normalize(self, message: dict[str, Any], meta: SyntheticSubscription) -> dict[str, Any]:
        bid, bid_qty = self._side(message, "best_5_buy_data")
        ask, ask_qty = self._side(message, "best_5_sell_data")
        payload = dict(message)
        payload.update(
            {
                "underlying": meta.underlying,
                "instrument_class": meta.instrument_class,
                "expiry": meta.expiry,
                "option_type": meta.option_type,
                "strike": meta.strike,
                "lot_size": meta.lot_size,
                "ltp": self._ltp(message),
                "bid": bid,
                "ask": ask,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
            }
        )
        return payload

    def _run_session(self) -> int:
        self.auth.login()
        by_exchange: dict[int, list[SyntheticSubscription]] = {}
        by_token: dict[tuple[int, str], SyntheticSubscription] = {}
        for item in self.subscriptions:
            by_exchange.setdefault(item.exchange_type, []).append(item)
            by_token[(item.exchange_type, item.token)] = item

        queue: Queue[tuple[int, dict[str, Any]]] = Queue()
        sockets: list[MarketDataWebSocket] = []

        def receive(exchange_type: int, message: Any) -> None:
            if isinstance(message, dict):
                queue.put((exchange_type, message))

        for exchange_type, items in sorted(by_exchange.items()):
            socket = MarketDataWebSocket(auth=self.auth)
            sockets.append(socket)
            Thread(
                target=socket.connect,
                kwargs={
                    "exchange_type": exchange_type,
                    "tokens": [item.token for item in items],
                    "mode": 3,
                    "correlation_id": f"synthetic-live-{exchange_type}",
                    "on_data": lambda msg, e=exchange_type: receive(e, msg),
                    "reconnect_attempts": 3,
                    "reconnect_delay_seconds": 2,
                },
                daemon=True,
            ).start()
        self._sockets = sockets

        catalog = HistoricalCatalog(self.data_db)
        recorder = LiveMarketDataRecorder(
            catalog,
            source=self.SOURCE,
            timeframe="1s",
            batch_size=self.batch_size,
        )
        latest: dict[tuple[int, str], tuple[int, dict[str, Any]]] = {}
        written = 0
        try:
            while self.market_open() and (not self.stop_event.is_set() or not queue.empty()):
                deadline = monotonic() + self.poll_seconds
                while monotonic() < deadline and (not self.stop_event.is_set() or not queue.empty()):
                    try:
                        exchange_type, raw = queue.get(
                            timeout=max(0.01, deadline - monotonic())
                        )
                    except Empty:
                        continue
                    token = str(raw.get("token") or "").strip()
                    meta = by_token.get((exchange_type, token))
                    if meta is None:
                        continue
                    timestamp = recorder._timestamp_ns(raw, time_ns())
                    second = (timestamp // 1_000_000_000) * 1_000_000_000
                    key = (exchange_type, token)
                    previous = latest.get(key)
                    if previous is not None:
                        previous_second = previous[0]
                        if second < previous_second:
                            continue
                        if second != previous_second:
                            payload = dict(previous[1])
                            payload["source_timestamp_ns"] = previous_second
                            written += recorder.on_tick(payload)
                            if self.on_observation is not None:
                                try:
                                    self.on_observation(dict(payload))
                                except Exception:
                                    pass
                    latest[key] = (second, self._normalize(raw, meta))
            for _, (timestamp, payload) in latest.items():
                payload["source_timestamp_ns"] = timestamp
                written += recorder.on_tick(payload)
                if self.on_observation is not None:
                    try:
                        self.on_observation(dict(payload))
                    except Exception:
                        pass
            written += recorder.flush()
        finally:
            catalog.close()
            for socket in sockets:
                socket.close()
            self._sockets = []
        return written

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.market_open():
                    self._run_session()
                else:
                    sleep(5)
            except Exception:
                sleep(10)

    def stop(self) -> None:
        self.stop_event.set()
        for socket in self._sockets:
            socket.close()


__all__ = ["LiveSyntheticOptionFutureRecorder", "SyntheticSubscription"]
