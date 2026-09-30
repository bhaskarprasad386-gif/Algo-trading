"""Continuous 1-second Calendar Spread collector using the common market-data feed.

Calendar Spread is a consumer of shared normalized futures data. It does not
open an Angel One WebSocket itself and shares subscriptions with other
strategies.
"""
from __future__ import annotations

from datetime import date, datetime, time
from threading import Lock
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.core.logger import app_logger
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.persistence import DailySQLiteMarketDataRepository
from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.ingestion import BoundedMarketDataIngestor

IST = ZoneInfo("Asia/Kolkata")
SOURCE = "angelone-calendar-live-1s"
TIMEFRAME = "1s"
EXCHANGE_TYPES = {"NFO": 2, "BFO": 4, "MCX": 5}
INSTRUMENT_TYPES = {
    "FUTIDX": "INDEX_FUTURE",
    "INDEX_FUTURE": "INDEX_FUTURE",
    "FUTSTK": "STOCK_FUTURE",
    "STOCK_FUTURE": "STOCK_FUTURE",
    "FUTCOM": "COMMODITY_FUTURE",
    "FUTCOMINDEX": "COMMODITY_FUTURE",
}


def _expiry(value: Any) -> date | None:
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _timestamp_ns(message: dict[str, Any]) -> int | None:
    try:
        n = int(float(message.get("exchange_timestamp")))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n < 100_000_000_000:
        return n * 1_000_000_000
    if n < 100_000_000_000_000:
        return n * 1_000_000
    if n < 100_000_000_000_000_000:
        return n * 1_000
    return n


def _side(message: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    levels = message.get(key)
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
        return None, None
    try:
        price = float(levels[0].get("price")) / 100.0
        quantity = float(levels[0].get("quantity"))
    except (TypeError, ValueError):
        return None, None
    return (price if price > 0 else None, quantity if quantity > 0 else None)


class LiveCalendarSpreadOneSecondCollector:
    """Collect all supported futures at 1-second source resolution."""

    def __init__(
        self,
        data_db: str,
        *,
        auth: AngelOneAuth | None = None,
        instrument_master: InstrumentMaster | None = None,
        poll_seconds: float = 0.25,
        on_observation=None,
        feed=None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.data_db = data_db
        self.auth = auth or AngelOneAuth()
        self.instrument_master = instrument_master or InstrumentMaster()
        self.poll_seconds = poll_seconds
        self.on_observation = on_observation
        self.stop_event = __import__("threading").Event()
        self._feed = feed
        self._latest: dict[tuple[str, str], tuple[int, Any]] = {}
        self._kind_by_key: dict[tuple[str, str], str] = {}
        self._lock = Lock()
        self._ingestor = None
        self._repository = None

    @staticmethod
    def market_open(now: datetime | None = None) -> bool:
        t = now or datetime.now(IST)
        return t.weekday() < 5 and time(9, 0) <= t.time() <= time(23, 30)

    @staticmethod
    def _exchange_open(exchange: str, value: datetime) -> bool:
        if value.weekday() >= 5:
            return False
        if exchange.upper() == "MCX":
            return time(9, 0) <= value.time() <= time(23, 30)
        return time(9, 15) <= value.time() <= time(15, 30)

    def _contracts(self) -> list[dict[str, Any]]:
        today = datetime.now(IST).date()
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in self.instrument_master.download():
            exchange = str(row.get("exch_seg") or "").upper()
            kind = INSTRUMENT_TYPES.get(str(row.get("instrumenttype") or "").upper())
            if exchange not in EXCHANGE_TYPES or not kind:
                continue
            expiry = _expiry(row.get("expiry"))
            token = str(row.get("token") or "").strip()
            symbol = str(row.get("symbol") or "").strip()
            underlying = str(row.get("name") or "").strip().upper()
            try:
                lot = int(str(row.get("lotsize") or row.get("lotSize") or "0"))
            except ValueError:
                continue
            if not expiry or expiry < today or not token or not symbol or not underlying or lot <= 0:
                continue
            grouped.setdefault((exchange, underlying), []).append({
                "exchange": exchange, "kind": kind, "expiry": expiry, "token": token,
                "symbol": symbol, "underlying": underlying, "lot_size": lot,
            })
        selected: list[dict[str, Any]] = []
        for rows in grouped.values():
            rows.sort(key=lambda x: (x["expiry"], x["symbol"], x["token"]))
            selected.extend(rows[:2])
        return selected

    @staticmethod
    def _descriptor(contract: dict[str, Any]):
        instrument_type = "commodity" if contract["kind"] == "COMMODITY_FUTURE" else "future"
        return CommonStrategyMarketFeed.descriptor(
            exchange=contract["exchange"],
            token=contract["token"],
            symbol=contract["symbol"],
            instrument_type=instrument_type,
            segment=contract["exchange"],
            expiry=contract["expiry"].isoformat(),
            lot_size=contract["lot_size"],
        )

    def _observe_record(self, record) -> None:
        exchange = record.instrument.exchange.strip().upper()
        local = datetime.fromtimestamp(record.timestamp_ns / 1_000_000_000, tz=ZoneInfo("UTC")).astimezone(IST)
        if not self._exchange_open(exchange, local):
            return
        key = (exchange, record.instrument.token.strip())
        second = (record.timestamp_ns // 1_000_000_000) * 1_000_000_000
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and previous[0] == second:
                return
            self._latest[key] = (second, record)
        if previous is not None:
            self._emit(previous[1], previous[0])

    def _emit(self, record, timestamp_ns: int) -> None:
        payload = record.as_dict()
        payload["timestamp_ns"] = timestamp_ns
        payload["source_timestamp_ns"] = timestamp_ns
        payload["exchange"] = record.instrument.exchange
        payload["instrument_type"] = record.instrument_type.value
        if record.expiry:
            payload["contract_month"] = record.expiry[:7]
        if self._ingestor is not None:
            self._ingestor.submit_historical(
                HistoricalRecord(
                    source=SOURCE,
                    instrument=f"{record.instrument.exchange}:{record.instrument.token}:{record.symbol}",
                    timeframe=TIMEFRAME,
                    timestamp_ns=timestamp_ns,
                    payload=payload,
                )
            )
        if self.on_observation is not None:
            try:
                self.on_observation(payload)
            except Exception as exc:
                app_logger.warning(f"Calendar Spread live scanner callback failed: {exc}")

    def _run_session(self) -> None:
        contracts = self._contracts()
        if not contracts:
            app_logger.warning("Calendar Spread live collector found no eligible futures")
            time_module.sleep(30)
            return
        self._kind_by_key = {(c["exchange"], c["token"]): c["kind"] for c in contracts}
        descriptors = tuple(self._descriptor(c) for c in contracts)
        self._repository = DailySQLiteMarketDataRepository(self.data_db)
        self._ingestor = BoundedMarketDataIngestor(
            self._repository,
            record_source=SOURCE,
        )
        self._ingestor.start()
        feed = self._feed or CommonStrategyMarketFeed(
            "calendar-spread",
            auth=self.auth,
        )
        self._feed = feed
        feed.start(descriptors, self._observe_record)
        try:
            while not self.stop_event.is_set() and self.market_open():
                time_module.sleep(self.poll_seconds)
            with self._lock:
                latest = list(self._latest.values())
                self._latest.clear()
            for timestamp, record in latest:
                self._emit(record, timestamp)
        finally:
            feed.stop()
            if self._ingestor is not None:
                self._ingestor.close()
            if self._repository is not None:
                self._repository.close()
            self._ingestor = None
            self._repository = None
            self._feed = None
        app_logger.info("Calendar Spread common-feed live session complete")

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.market_open():
                    self._run_session()
                else:
                    time_module.sleep(5)
            except Exception as exc:
                app_logger.error(f"Calendar Spread common-feed collector failed: {exc}")
                time_module.sleep(10)

    def snapshot(self) -> dict[str, Any]:
        """Return lightweight runtime state for the shared-feed health endpoint."""
        feed = self._feed
        ingestor = self._ingestor
        return {
            "running": not self.stop_event.is_set(),
            "registered_instruments": len(self._kind_by_key),
            "feed": None if feed is None else feed.snapshot(),
            "ingestor": None if ingestor is None else ingestor.snapshot(),
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveCalendarSpreadOneSecondCollector", "_expiry", "_timestamp_ns"]
