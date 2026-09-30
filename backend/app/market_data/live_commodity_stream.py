"""Bounded 1-second commodity recorder over the shared Angel One WebSocket feed."""
from __future__ import annotations

from datetime import datetime, time
from threading import Event, Lock
from time import sleep, time_ns
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.persistence import DailySQLiteMarketDataRepository
from app.market_data.instruments import InstrumentMaster
from app.backtesting.historical_catalog import HistoricalRecord
from .commodity_subscriptions import select_commodity_contracts

IST = ZoneInfo("Asia/Kolkata")


class LiveCommodityMarketDataRecorder:
    """Record Angel One commodity futures/options without strategy-owned sockets."""

    SOURCE = "angelone-commodity-live-1s"

    def __init__(
        self,
        data_db: str,
        *,
        underlyings: Iterable[str],
        atm_provider: Callable[[str, int], float | None] | None = None,
        instrument_master: InstrumentMaster | None = None,
        auth: AngelOneAuth | None = None,
        on_observation: Callable[[dict[str, Any]], None] | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        names = tuple(dict.fromkeys(str(x).strip().upper() for x in underlyings if str(x).strip()))
        if not names:
            raise ValueError("at least one commodity underlying is required")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.data_db = data_db
        self.underlyings = names
        self.atm_provider = atm_provider
        self.instrument_master = instrument_master or InstrumentMaster()
        self.auth = auth or AngelOneAuth()
        self.on_observation = on_observation
        self.poll_seconds = poll_seconds
        self.stop_event = Event()
        self._feed: CommonStrategyMarketFeed | None = None
        self._repository = None
        self._ingestor = None
        self._latest: dict[tuple[str, str], tuple[int, Any]] = {}
        self._metadata: dict[tuple[str, str], Any] = {}
        self._lock = Lock()

    @staticmethod
    def market_open(now: datetime | None = None) -> bool:
        current = now or datetime.now(IST)
        return current.weekday() < 5 and time(9, 0) <= current.time() <= time(23, 30)

    def build_subscriptions(self) -> tuple:
        self.instrument_master.download()
        out = []
        seen: set[tuple[int, str]] = set()
        for symbol in self.underlyings:
            atm = self.atm_provider(symbol, time_ns()) if self.atm_provider else None
            selection = select_commodity_contracts(
                self.instrument_master.instruments,
                underlying=symbol,
                atm_strike=atm,
                strike_count=10,
            )
            for item in selection.subscriptions:
                key = (item.exchange_type, item.token)
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        return tuple(out)

    def _descriptor(self, item):
        return CommonStrategyMarketFeed.descriptor(
            exchange={5: "MCX", 7: "NCDEX"}[item.exchange_type],
            token=item.token,
            symbol=item.symbol,
            instrument_type="option" if item.option_type else "commodity",
            segment={5: "MCX", 7: "NCDEX"}[item.exchange_type],
            expiry=item.expiry,
            strike=item.strike,
            option_type=item.option_type,
            lot_size=item.lot_size,
        )

    def _observe(self, record) -> None:
        key = (record.instrument.exchange.strip().upper(), record.instrument.token.strip())
        second = (record.timestamp_ns // 1_000_000_000) * 1_000_000_000
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and second <= previous[0]:
                if second == previous[0]:
                    self._latest[key] = (second, record)
                return
            self._latest[key] = (second, record)
        if previous is not None:
            self._emit(previous[1], previous[0])

    def _emit(self, record, timestamp_ns: int) -> None:
        payload = record.as_dict()
        payload.update({
            "timestamp_ns": timestamp_ns,
            "source_timestamp_ns": timestamp_ns,
            "exchange": record.instrument.exchange,
            "underlying": record.symbol,
            "instrument_class": "COMMODITY",
        })
        meta = next(
            (m for k, m in self._metadata.items()
             if k == (record.instrument.exchange.strip().upper(), record.instrument.token.strip())),
            None,
        )
        if meta is not None:
            payload.update({
                "underlying": meta.underlying,
                "expiry": meta.expiry,
                "option_type": meta.option_type,
                "strike": meta.strike,
                "lot_size": meta.lot_size,
            })
        if self._ingestor is not None:
            self._ingestor.submit_historical(HistoricalRecord(
                source=self.SOURCE,
                instrument=f"{record.instrument.exchange}:{record.instrument.token}:{record.symbol}",
                timeframe="1s",
                timestamp_ns=timestamp_ns,
                payload=payload,
            ))
        if self.on_observation is not None:
            try:
                self.on_observation(dict(payload))
            except Exception:
                pass

    def _run_session(self) -> None:
        subscriptions = self.build_subscriptions()
        if not subscriptions:
            sleep(30)
            return
        self._metadata = {
            (("MCX" if x.exchange_type == 5 else "NCDEX"), x.token): x
            for x in subscriptions
        }
        self._repository = DailySQLiteMarketDataRepository(self.data_db)
        self._ingestor = BoundedMarketDataIngestor(self._repository, record_source=self.SOURCE)
        self._ingestor.start()
        self._feed = CommonStrategyMarketFeed("commodity-market-data", auth=self.auth)
        descriptors = tuple(self._descriptor(x) for x in subscriptions)
        self._feed.start(descriptors, self._observe)
        try:
            while not self.stop_event.is_set() and self.market_open():
                sleep(self.poll_seconds)
            with self._lock:
                latest = list(self._latest.values())
                self._latest.clear()
            for timestamp, record in latest:
                self._emit(record, timestamp)
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
            except Exception:
                sleep(10)

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveCommodityMarketDataRecorder"]
