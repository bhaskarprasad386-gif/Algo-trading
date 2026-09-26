"""Reusable live market-data recorder for paper trading and future backtests.

The recorder is strategy-neutral: broker ticks are persisted as source-backed
event records in the existing durable HistoricalCatalog. Strategy-specific
derived data (Cash-Future gap, option spreads, indicators, etc.) stays outside
this layer.
"""

from __future__ import annotations

from threading import Lock
from time import time_ns
from typing import Any

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.high_resolution import EVENT_TIMEFRAME


class LiveMarketDataRecorder:
    """Batch-persist normalized live ticks without retaining full history in RAM."""

    SOURCE = "angelone-live"

    def __init__(
        self,
        catalog: HistoricalCatalog,
        *,
        source: str = SOURCE,
        timeframe: str = EVENT_TIMEFRAME,
        batch_size: int = 256,
    ) -> None:
        if not source.strip():
            raise ValueError("source is required")
        if not timeframe.strip():
            raise ValueError("timeframe is required")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.catalog = catalog
        self.source = source.strip()
        self.timeframe = timeframe.strip()
        self.batch_size = batch_size
        self._pending: list[HistoricalRecord] = []
        self._lock = Lock()

    @staticmethod
    def _timestamp_ns(message: dict[str, Any], fallback_ns: int) -> int:
        for key in (
            "exchange_timestamp_ns",
            "exchangeTimestampNs",
            "exchange_timestamp",
            "exchangeTimestamp",
            "last_traded_timestamp",
            "lastTradedTimestamp",
            "timestamp",
        ):
            value = message.get(key)
            if value is None:
                continue
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                continue
            if number <= 0:
                continue
            # Angel One timestamp fields may be milliseconds/seconds; normalize
            # only when the value is clearly below nanosecond magnitude.
            if number < 10_000_000_000:
                number *= 1_000_000_000
            elif number < 10_000_000_000_000:
                number *= 1_000_000
            elif number < 10_000_000_000_000_000:
                number *= 1_000
            return number
        return fallback_ns

    @staticmethod
    def _instrument(message: dict[str, Any]) -> str:
        token = str(message.get("token") or "").strip()
        symbol = str(
            message.get("symbol")
            or message.get("tradingSymbol")
            or message.get("trading_symbol")
            or ""
        ).strip()
        if not token and not symbol:
            raise ValueError("live tick requires token or symbol")
        return f"{symbol}|{token}" if symbol and token else symbol or token

    def on_tick(self, message: dict[str, Any]) -> int:
        """Accept one broker tick and flush a bounded batch when full.

        Returns the number of records durably inserted by this call. A zero
        return means the record remains in the bounded in-memory batch.
        """
        if not isinstance(message, dict):
            raise ValueError("live tick must be a mapping")
        received_ns = time_ns()
        instrument = self._instrument(message)
        timestamp_ns = self._timestamp_ns(message, received_ns)
        if timestamp_ns < 0:
            raise ValueError("live tick timestamp cannot be negative")
        record = HistoricalRecord(
            source=self.source,
            instrument=instrument,
            timeframe=self.timeframe,
            timestamp_ns=timestamp_ns,
            payload=dict(message),
        )
        with self._lock:
            self._pending.append(record)
            if len(self._pending) < self.batch_size:
                return 0
            batch = tuple(self._pending)
            self._pending.clear()
        return self.catalog.ingest_events(batch, ingested_at_ns=received_ns)

    def flush(self) -> int:
        """Durably persist all currently buffered ticks and clear the buffer."""
        with self._lock:
            if not self._pending:
                return 0
            batch = tuple(self._pending)
            self._pending.clear()
        return self.catalog.ingest_events(batch, ingested_at_ns=time_ns())

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
