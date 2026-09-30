"""Bounded canonical market-data ingestion with durable repository writes."""
from __future__ import annotations
import os
from queue import Empty
from threading import Event, Lock, Thread
from time import monotonic_ns, time_ns
from typing import Callable, Iterable, Protocol, TypeAlias
from app.backtesting.historical_catalog import HistoricalRecord
from .bounded_buffer import BoundedPriorityBuffer, BufferPriority
from .contracts import MarketDataRecord
class MarketDataWriteRepository(Protocol):
    def write_batch(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int: ...
PersistableRecord: TypeAlias = MarketDataRecord | HistoricalRecord
class BoundedMarketDataIngestor:
    """Hard-bounded queue; critical data backpressures, normal data may drop."""
    def __init__(self, repository: MarketDataWriteRepository | object, *, max_queue=5000, batch_size=100,
                 flush_seconds=5.0, put_timeout_seconds=1.0, record_source="angelone-live-1s",
                 on_error: Callable[[Exception], None] | None = None):
        for name,value in (("max_queue",max_queue),("batch_size",batch_size)):
            if isinstance(value,bool) or not isinstance(value,int) or value<1: raise ValueError(f"{name} must be a positive integer")
        if flush_seconds<=0 or put_timeout_seconds<=0: raise ValueError("timeouts must be positive")
        self.repository=repository; self.max_queue=max_queue; self.batch_size=batch_size
        self.flush_seconds=float(flush_seconds); self.put_timeout_seconds=float(put_timeout_seconds)
        self.record_source=str(record_source).strip(); self.on_error=on_error
        if not self.record_source: raise ValueError("record_source is required")
        self._queue=BoundedPriorityBuffer[PersistableRecord](maxsize=max_queue,critical_timeout_seconds=self.put_timeout_seconds,normal_timeout_seconds=self.put_timeout_seconds)
        self._stop=Event(); self._lock=Lock(); self._started=False; self._thread=None
        self._accepted=self._inserted=self._errors=self._dropped=self._critical_timeouts=self._batches=0
        self._latency=self._max_latency=0.0; self._started_ns=None
    def start(self):
        with self._lock:
            if self._started:return
            self._started=True; self._started_ns=monotonic_ns(); self._stop.clear()
            self._thread=Thread(target=self._run,name="market-data-writer",daemon=True); self._thread.start()
    @staticmethod
    def _historical(record,source):
        if isinstance(record,HistoricalRecord):return record
        return HistoricalRecord(source=source,instrument=record.instrument.value,timeframe=record.timeframe,timestamp_ns=record.timestamp_ns,payload=record.as_dict())
    def _submit(self,record,priority):
        if not isinstance(record,(MarketDataRecord,HistoricalRecord)):raise TypeError("record must be a MarketDataRecord or HistoricalRecord")
        self.start(); accepted=self._queue.put(record,priority=priority)
        with self._lock:
            if accepted:self._accepted+=1
            else:
                self._dropped+=1
                if priority is BufferPriority.CRITICAL:self._critical_timeouts+=1
        if not accepted and priority is BufferPriority.CRITICAL:raise TimeoutError("critical market-data ingestion queue is full")
        return accepted
    def submit(self,record:MarketDataRecord,*,priority=BufferPriority.CRITICAL):return self._submit(record,priority)
    def submit_historical(self,record:HistoricalRecord,*,priority=BufferPriority.CRITICAL):return self._submit(record,priority)
    def _write_batch(self,batch):
        records=[self._historical(r,self.record_source) for r in batch]; started=monotonic_ns()
        writer=getattr(self.repository,"write_batch",None)
        if callable(writer):inserted=int(writer(records,ingested_at_ns=time_ns()))
        else:
            legacy=getattr(self.repository,"ingest_if_absent_batch",None)
            if not callable(legacy):raise TypeError("repository must implement write_batch or ingest_if_absent_batch")
            inserted=int(legacy(records,ingested_at_ns=time_ns()))
        latency=(monotonic_ns()-started)/1_000_000.0
        with self._lock:
            self._inserted+=inserted; self._batches+=1; self._latency+=latency; self._max_latency=max(self._max_latency,latency)
    def _report(self,exc):
        with self._lock:self._errors+=1
        if self.on_error:self.on_error(exc)
    def _run(self):
        batch=[]; deadline=monotonic_ns()+int(self.flush_seconds*1_000_000_000)
        while not self._stop.is_set() or self._queue.qsize()>0:
            try:item=self._queue.get(timeout=max(.01,(deadline-monotonic_ns())/1_000_000_000))
            except Empty:item=None
            if item is not None:batch.append(item);self._queue.task_done()
            if batch and (len(batch)>=self.batch_size or monotonic_ns()>=deadline):
                try:self._write_batch(batch);batch.clear()
                except Exception as exc:self._report(exc)
                deadline=monotonic_ns()+int(self.flush_seconds*1_000_000_000)
        if batch:
            try:self._write_batch(batch)
            except Exception as exc:self._report(exc)
    @staticmethod
    def _rss_bytes():
        try:
            with open("/proc/self/statm",encoding="utf-8") as f:pages=int(f.read().split()[1])
            return pages*os.sysconf("SC_PAGE_SIZE")
        except (OSError,ValueError,IndexError):return None
    def close(self,timeout=10.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(.1,float(timeout)))
            if self._thread.is_alive():raise TimeoutError("market-data writer did not stop before timeout")
        with self._lock:self._started=False;self._thread=None
    def snapshot(self):
        with self._lock:
            elapsed=None if self._started_ns is None else max(.001,(monotonic_ns()-self._started_ns)/1_000_000_000)
            q=self._queue.qsize()
            return {"queue_depth":q,"queue_capacity":self.max_queue,"queue_utilization":q/self.max_queue,
                    "accepted":self._accepted,"inserted":self._inserted,"dropped":self._dropped,"critical_timeouts":self._critical_timeouts,
                    "errors":self._errors,"batches":self._batches,"db_write_latency_ms":self._latency/self._batches if self._batches else 0.0,
                    "max_db_write_latency_ms":self._max_latency,"records_per_second":self._accepted/elapsed if elapsed else 0.0,
                    "memory_rss_bytes":self._rss_bytes(),"running":bool(self._thread and self._thread.is_alive())}
