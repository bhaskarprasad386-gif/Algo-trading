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
from app.core.config import settings
from app.core.logger import app_logger
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.persistence import DailySQLiteMarketDataRepository
from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.bounded_buffer import BufferPriority

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
        self._stats = {
            "records_received": 0,
            "records_emitted": 0,
            "out_of_session_dropped": 0,
            "out_of_order_dropped": 0,
            "same_second_updates": 0,
            "same_second_weaker_dropped": 0,
            "persistence_errors": 0,
            "persistence_dropped": 0,
            "callback_errors": 0,
        }

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
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
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
            grouped.setdefault((exchange, underlying, kind), []).append({
                "exchange": exchange, "kind": kind, "expiry": expiry, "token": token,
                "symbol": symbol, "underlying": underlying, "lot_size": lot,
            })
        selected: list[dict[str, Any]] = []
        for rows in grouped.values():
            # Keep at most one contract per expiry. Duplicate master rows for
            # the same month must not consume both near/far slots.
            by_expiry: dict[date, dict[str, Any]] = {}
            for row in sorted(rows, key=lambda x: (x["expiry"], x["symbol"], x["token"])):
                by_expiry.setdefault(row["expiry"], row)
            expiries = sorted(by_expiry)
            selected.extend(by_expiry[expiry] for expiry in expiries[:2])
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
            underlying=contract["underlying"],
        )

    def _observe_record(self, record) -> None:
        exchange = record.instrument.exchange.strip().upper()
        with self._lock:
            self._stats["records_received"] += 1
        local = datetime.fromtimestamp(record.timestamp_ns / 1_000_000_000, tz=ZoneInfo("UTC")).astimezone(IST)
        if not self._exchange_open(exchange, local):
            with self._lock:
                self._stats["out_of_session_dropped"] += 1
            return
        key = (exchange, record.instrument.token.strip())
        second = (record.timestamp_ns // 1_000_000_000) * 1_000_000_000
        previous_to_emit = None
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and second < previous[0]:
                self._stats["out_of_order_dropped"] += 1
                return
            if previous is not None and previous[0] == second:
                old_record = previous[1]
                old_valid = bool(getattr(old_record, "is_executable_quote", False))
                new_valid = bool(getattr(record, "is_executable_quote", False))
                if record.timestamp_ns <= old_record.timestamp_ns:
                    self._stats["out_of_order_dropped"] += 1
                    return
                # Preserve an executable quote if a later tick in this second
                # has missing depth; otherwise keep the newest observation.
                if old_valid and not new_valid:
                    self._stats["same_second_weaker_dropped"] += 1
                    return
                self._latest[key] = (second, record)
                self._stats["same_second_updates"] += 1
                return
            self._latest[key] = (second, record)
            if previous is not None:
                previous_to_emit = previous
        if previous_to_emit is not None:
            self._emit(previous_to_emit[1], previous_to_emit[0])

    def _emit(self, record, timestamp_ns: int) -> None:
        payload = record.as_dict()
        payload["timestamp_ns"] = timestamp_ns
        payload["source_timestamp_ns"] = timestamp_ns
        payload["exchange"] = record.instrument.exchange
        payload["instrument_type"] = record.instrument_type.value
        if record.expiry:
            payload["contract_month"] = record.expiry[:7]
        with self._lock:
            self._stats["records_emitted"] += 1
        # Deliver to the in-memory scanner first. Optional disk persistence is
        # best-effort and uses a short-timeout NORMAL queue entry below.
        if self.on_observation is not None:
            try:
                self.on_observation(payload)
            except Exception as exc:
                with self._lock:
                    self._stats["callback_errors"] += 1
                app_logger.warning(f"Calendar Spread live scanner callback failed: {exc}")
        if self._ingestor is not None:
            try:
                accepted = self._ingestor.submit_historical(
                    HistoricalRecord(
                        source=SOURCE,
                        instrument=f"{record.instrument.exchange}:{record.instrument.token}:{record.symbol}",
                        timeframe=TIMEFRAME,
                        timestamp_ns=timestamp_ns,
                        payload=payload,
                    ),
                    priority=BufferPriority.NORMAL,
                )
                if accepted is False:
                    with self._lock:
                        self._stats["persistence_dropped"] += 1
            except Exception as exc:
                # Persistence is optional; a failed writer must never prevent
                # the in-memory live scanner from receiving ticks.
                with self._lock:
                    self._stats["persistence_errors"] += 1
                app_logger.error(f"Calendar Spread persistence enqueue failed after live callback: {exc}")

    def _run_session(self) -> None:
        contracts = self._contracts()
        if not contracts:
            app_logger.warning("Calendar Spread live collector found no eligible futures")
            self.stop_event.wait(30)
            return
        self._kind_by_key = {(c["exchange"], c["token"]): c["kind"] for c in contracts}
        descriptors = tuple(self._descriptor(c) for c in contracts)
        feed = self._feed or CommonStrategyMarketFeed(
            "calendar-spread",
            auth=self.auth,
        )
        self._feed = feed
        try:
            # Guard startup too: feed.start() can partially allocate resources
            # before raising, so it must be covered by the same cleanup path.
            if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED:
                self._repository = DailySQLiteMarketDataRepository(self.data_db)
                self._ingestor = BoundedMarketDataIngestor(
                    self._repository,
                    record_source=SOURCE,
                    # Persistence is optional and must not stall live scanning
                    # behind a saturated disk-writer queue.
                    put_timeout_seconds=0.01,
                )
                self._ingestor.start()
            feed.start(descriptors, self._observe_record)
            while not self.stop_event.is_set() and self.market_open():
                time_module.sleep(self.poll_seconds)
            with self._lock:
                latest = list(self._latest.values())
                self._latest.clear()
            for timestamp, record in latest:
                self._emit(record, timestamp)
        finally:
            # One broken cleanup operation must not prevent the remaining
            # resources from being released.
            for resource, method, label in (
                (feed, "stop", "feed"),
                (self._ingestor, "close", "ingestor"),
                (self._repository, "close", "repository"),
            ):
                if resource is None:
                    continue
                try:
                    getattr(resource, method)()
                except Exception as exc:
                    app_logger.error(f"Calendar Spread {label} cleanup failed: {exc}")
            self._ingestor = None
            self._repository = None
            self._feed = None
        app_logger.info("Calendar Spread common-feed live session complete")

    def run_forever(self) -> None:
        # Let the application-level supervisor observe worker failures and apply
        # one consistent bounded exponential retry policy. Swallowing exceptions
        # here would trap the supervisor behind a fixed internal 10-second loop.
        while not self.stop_event.is_set():
            if self.market_open():
                self._run_session()
            else:
                time_module.sleep(5)

    def snapshot(self) -> dict[str, Any]:
        """Return lightweight runtime state for the shared-feed health endpoint."""
        feed = self._feed
        ingestor = self._ingestor
        with self._lock:
            stats = dict(self._stats)
            latest_buckets = len(self._latest)
        return {
            "running": not self.stop_event.is_set(),
            "registered_instruments": len(self._kind_by_key),
            "latest_instrument_buckets": latest_buckets,
            "feed": None if feed is None else feed.snapshot(),
            "ingestor": None if ingestor is None else ingestor.snapshot(),
            "diagnostics": stats,
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveCalendarSpreadOneSecondCollector", "_expiry", "_timestamp_ns"]
