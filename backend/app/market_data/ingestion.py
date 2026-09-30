"""Bounded canonical market-data ingestion with durable daily-shard writes."""
from __future__ import annotations

from queue import Full, Queue
from threading import Event, Lock, Thread
from time import monotonic_ns, time_ns
from typing import Callable

from app.backtesting.historical_catalog import HistoricalRecord
from .contracts import MarketDataRecord
from .daily_shard_catalog import DailyMarketDataShardCatalog

LIVE_SOURCE = "angelone-live-1s"


class BoundedMarketDataIngestor:
    """Bound websocket delivery memory and batch durable writes.

    The producer blocks briefly when the bounded queue is full instead of
    silently dropping a required 1-second record. The writer commits batches
    by size or time and flushes on close.
    """

    def __init__(
        self,
        catalog: DailyMarketDataShardCatalog,
        *,
        max_queue: int = 5000,
        batch_size: int = 100,
        flush_seconds: float = 5.0,
        put_timeout_seconds: float = 1.0,
        record_source: str = LIVE_SOURCE,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        for name, value in (
            ("max_queue", max_queue),
            ("batch_size", batch_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if flush_seconds <= 0 or put_timeout_seconds <= 0:
            raise ValueError("flush_seconds and put_timeout_seconds must be positive")
        if not str(record_source).strip():
            raise ValueError("record_source is required")
        self.catalog = catalog
        self._queue: Queue[MarketDataRecord | None] = Queue(maxsize=max_queue)
        self.batch_size = batch_size
        self.flush_seconds = float(flush_seconds)
        self.put_timeout_seconds = float(put_timeout_seconds)
        self.record_source = str(record_source).strip()
        self.on_error = on_error
        self._stop = Event()
        self._started = False
        self._thread: Thread | None = None
        self._lock = Lock()
        self._accepted = 0
        self._inserted = 0
        self._errors = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._thread = Thread(target=self._run, name="market-data-writer", daemon=True)
            self._thread.start()

    def submit(self, record: MarketDataRecord) -> None:
        if not isinstance(record, MarketDataRecord):
            raise TypeError("record must be a MarketDataRecord")
        self.start()
        try:
            self._queue.put(record, timeout=self.put_timeout_seconds)
        except Full as exc:
            raise TimeoutError("market-data ingestion queue is full") from exc
        with self._lock:
            self._accepted += 1

    @staticmethod
    def _historical(record: MarketDataRecord, source: str) -> HistoricalRecord:
        return HistoricalRecord(
            source=source,
            instrument=record.instrument.value,
            timeframe=record.timeframe,
            timestamp_ns=record.timestamp_ns,
            payload=record.as_dict(),
        )

    def _flush(self, batch: list[MarketDataRecord]) -> None:
        if not batch:
            return
        records = [self._historical(record, self.record_source) for record in batch]
        inserted = self.catalog.ingest_if_absent_batch(records, ingested_at_ns=time_ns())
        with self._lock:
            self._inserted += inserted

    def _report(self, exc: Exception) -> None:
        with self._lock:
            self._errors += 1
        if self.on_error is not None:
            self.on_error(exc)

    def _run(self) -> None:
        batch: list[MarketDataRecord] = []
        deadline = monotonic_ns() + int(self.flush_seconds * 1_000_000_000)
        while not self._stop.is_set() or not self._queue.empty():
            timeout = max(0.01, (deadline - monotonic_ns()) / 1_000_000_000)
            try:
                item = self._queue.get(timeout=timeout)
            except Exception:
                item = None
            if item is not None:
                batch.append(item)
                self._queue.task_done()
            if batch and (len(batch) >= self.batch_size or monotonic_ns() >= deadline):
                try:
                    self._flush(batch)
                    batch.clear()
                except Exception as exc:
                    self._report(exc)
                deadline = monotonic_ns() + int(self.flush_seconds * 1_000_000_000)
        if batch:
            try:
                self._flush(batch)
            except Exception as exc:
                self._report(exc)

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.1, float(timeout)))
            if thread.is_alive():
                raise TimeoutError("market-data writer did not stop before timeout")
        with self._lock:
            self._started = False
            self._thread = None

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "accepted": self._accepted,
                "inserted": self._inserted,
                "errors": self._errors,
                "running": bool(self._thread and self._thread.is_alive()),
            }
