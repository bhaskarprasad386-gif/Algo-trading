"""Continuous option/future recorder using the shared common WebSocket feed.

Concrete contracts remain selected by the strategy, but broker connectivity,
normalization and durable persistence are shared and bounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from threading import Event, Lock
from time import sleep
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.core.config import settings
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.persistence import DailySQLiteMarketDataRepository
from app.market_data.websocket import MarketDataWebSocket
from app.market_data.common_websocket import CommonWebSocketManager
from app.core.logger import app_logger

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 40)

_EXCHANGE_BY_TYPE = {1: "NSE", 2: "NFO", 3: "BSE", 4: "BFO", 5: "MCX", 7: "NCDEX"}


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
    contract_role: str = "CURRENT"


class LiveSyntheticOptionFutureRecorder:
    """Persist live option/future ticks through the common market-data layer."""

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
        manager: CommonWebSocketManager | None = None,
        consumer: str = "synthetic-options",
    ) -> None:
        if not subscriptions:
            raise ValueError("at least one synthetic subscription is required")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        normalized = []
        seen: set[tuple[int, str]] = set()
        for item in subscriptions:
            token = str(item.token).strip()
            symbol = str(item.symbol).strip()
            underlying = str(item.underlying).strip().upper()
            cls = str(item.instrument_class).strip().upper()
            if not token or not symbol or not underlying:
                raise ValueError("subscription token, symbol and underlying are required")
            if cls not in {"STOCK", "INDEX", "COMMODITY"}:
                raise ValueError("instrument_class must be STOCK, INDEX or COMMODITY")
            key = (int(item.exchange_type), token)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                SyntheticSubscription(
                    int(item.exchange_type), token, symbol, underlying, cls,
                    item.expiry, item.option_type, item.strike, item.lot_size, item.contract_role,
                )
            )
        self.data_db = data_db
        self.subscriptions = tuple(normalized)
        self.auth = auth or AngelOneAuth()
        self.batch_size = batch_size
        self.poll_seconds = poll_seconds
        self.on_observation = on_observation
        self._manager = manager
        self.consumer = str(consumer).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        self.stop_event = Event()
        self._feed: CommonStrategyMarketFeed | None = None
        self._repository: DailySQLiteMarketDataRepository | None = None
        self._ingestor: BoundedMarketDataIngestor | None = None
        self._latest: dict[tuple[str, str], tuple[int, Any]] = {}
        self._lock = Lock()
        self._metadata: dict[tuple[str, str], SyntheticSubscription] = {}
        self._callback_errors = 0

    @staticmethod
    def market_open(now: datetime | None = None) -> bool:
        current = now or datetime.now(IST)
        return current.weekday() < 5 and OPEN <= current.time() <= CLOSE

    @staticmethod
    def _normalize(message: dict[str, Any], meta: SyntheticSubscription) -> dict[str, Any]:
        def price(value):
            try:
                value = float(value) / 100.0
                return value if value > 0 else None
            except (TypeError, ValueError):
                return None
        def side(name, field):
            levels = message.get(name)
            if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
                return None
            try:
                value = float(levels[0].get(field))
                return value if value > 0 else None
            except (TypeError, ValueError):
                return None
        bid = side("best_5_buy_data", "price")
        ask = side("best_5_sell_data", "price")
        return {
            **message,
            "underlying": meta.underlying,
            "instrument_class": meta.instrument_class,
            "expiry": meta.expiry,
            "option_type": meta.option_type,
            "strike": meta.strike,
            "lot_size": meta.lot_size,
            "ltp": price(message.get("last_traded_price")),
            "bid": None if bid is None else bid / 100.0,
            "ask": None if ask is None else ask / 100.0,
            "bid_qty": side("best_5_buy_data", "quantity"),
            "ask_qty": side("best_5_sell_data", "quantity"),
        }

    def _descriptors(self):
        descriptors = []
        for item in self.subscriptions:
            exchange = _EXCHANGE_BY_TYPE.get(item.exchange_type)
            if exchange is None:
                raise ValueError(f"unsupported exchange type: {item.exchange_type}")
            key = (exchange, item.token)
            self._metadata[key] = item
            descriptors.append(
                CommonStrategyMarketFeed.descriptor(
                    exchange=exchange,
                    token=item.token,
                    symbol=item.symbol,
                    instrument_type="option" if item.option_type else "future",
                    segment=exchange,
                    expiry=item.expiry,
                    strike=item.strike,
                    option_type=item.option_type,
                    lot_size=item.lot_size,
                )
            )
        return descriptors

    def _emit(self, record, timestamp_ns: int) -> None:
        payload = record.as_dict()
        payload["timestamp_ns"] = timestamp_ns
        payload["source_timestamp_ns"] = timestamp_ns
        meta = self._metadata.get(
            (record.instrument.exchange.strip().upper(), record.instrument.token.strip())
        )
        if meta is not None:
            payload.update({
                "underlying": meta.underlying,
                "instrument_class": meta.instrument_class,
                "expiry": meta.expiry,
                "option_type": meta.option_type,
                "strike": meta.strike,
                "lot_size": meta.lot_size,
                "contract_role": meta.contract_role,
            })
        if self.on_observation is not None:
            try:
                self.on_observation(dict(payload))
            except Exception as exc:
                with self._lock:
                    self._callback_errors += 1
                app_logger.exception(
                    "Synthetic observation callback failed for %s token=%s: %s",
                    record.symbol, record.instrument.token, exc,
                )
        if self._ingestor is not None:
            self._ingestor.submit_historical(
                HistoricalRecord(
                    source=self.SOURCE,
                    instrument=f"{record.symbol}|{record.instrument.token}",
                    timeframe="1s",
                    timestamp_ns=timestamp_ns,
                    payload=payload,
                )
            )

    def _on_record(self, record) -> None:
        key = (record.instrument.exchange.strip().upper(), record.instrument.token.strip())
        second = (record.timestamp_ns // 1_000_000_000) * 1_000_000_000
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and second < previous[0]:
                return
            if previous is not None and second == previous[0]:
                self._latest[key] = (second, record)
                return
            self._latest[key] = (second, record)
        if previous is not None:
            # Persist the latest tick's real exchange timestamp, not the
            # beginning-of-second bucket key. Multiple ticks in one second
            # must coalesce to the newest tick without falsifying its time.
            self._emit(previous[1], previous[1].timestamp_ns)

    def _run_session(self) -> int:
        if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED:
            self._repository = DailySQLiteMarketDataRepository(self.data_db)
            self._ingestor = BoundedMarketDataIngestor(
                self._repository,
                batch_size=self.batch_size,
                record_source=self.SOURCE,
            )
            self._ingestor.start()
        feed_kwargs = {"auth": self.auth}
        if self._manager is not None:
            feed_kwargs["manager"] = self._manager
        else:
            feed_kwargs["socket_factory"] = lambda: MarketDataWebSocket(auth=self.auth)
        self._feed = CommonStrategyMarketFeed(self.consumer, **feed_kwargs)
        self._feed.start(self._descriptors(), self._on_record)
        try:
            while self.market_open() and not self.stop_event.is_set():
                sleep(self.poll_seconds)
            with self._lock:
                latest = list(self._latest.values())
                self._latest.clear()
            for _, record in latest:
                self._emit(record, record.timestamp_ns)
            if self._ingestor is not None:
                self._ingestor.close()
                snapshot = self._ingestor.snapshot()
                return int(snapshot.get("inserted", 0))
            return 0
        finally:
            if self._feed:
                self._feed.stop()
                self._feed = None
            if self._ingestor:
                self._ingestor.close()
                self._ingestor = None
            if self._repository:
                self._repository.close()
                self._repository = None

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.market_open():
                    self._run_session()
                else:
                    sleep(5)
            except Exception as exc:
                app_logger.exception("Synthetic option/future recorder session failed: %s", exc)
                sleep(10)

    def snapshot(self) -> dict[str, Any]:
        """Return lightweight recorder state for runtime diagnostics."""
        with self._lock:
            latest = len(self._latest)
        feed = self._feed
        return {
            "running": not self.stop_event.is_set(),
            "subscriptions": len(self.subscriptions),
            "latest_instruments": latest,
            "callback_errors": self._callback_errors,
            "feed": None if feed is None else feed.snapshot(),
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveSyntheticOptionFutureRecorder", "SyntheticSubscription"]
