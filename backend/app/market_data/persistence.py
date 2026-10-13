"""Strategy-neutral repository over daily SQLite market-data shards."""
from __future__ import annotations
from pathlib import Path
from threading import Lock
from time import monotonic_ns
from typing import Iterable,Protocol
from app.backtesting.historical_catalog import HistoricalRecord
from .daily_shard_catalog import DailyMarketDataShardCatalog

# Multiple live strategies may create independent repositories for the same
# daily shard. SQLite permits only one writer per database file, so serialize
# writes across repository instances that share the same logical base path.
_WRITE_LOCKS_GUARD = Lock()
_WRITE_LOCKS: dict[str, Lock] = {}


def _write_lock_for(base_path: str | Path) -> Lock:
    key = str(Path(base_path).expanduser().resolve())
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _WRITE_LOCKS[key] = lock
        return lock
class MarketDataRepository(Protocol):
    def write_batch(self,records:Iterable[HistoricalRecord],*,ingested_at_ns:int=0)->int:...
    def checkpoint(self,*,mode:str="PASSIVE")->tuple[int,int,int]:...
    def prune_shards_older_than(self,*,retention_days:int,today=None):...
    def close(self)->None:...
class DailySQLiteMarketDataRepository:
    def __init__(self,base_path:str)->None:
        self.catalog=DailyMarketDataShardCatalog(base_path);self._write_lock=_write_lock_for(self.catalog.base_path);self._lock=Lock();self._batches=0;self._seen=0;self._inserted=0;self._latency=0.;self._max_latency=0.
    def write_batch(self,records,*,ingested_at_ns=0):
        batch=tuple(records)
        if not batch:return 0
        started=monotonic_ns()
        with self._write_lock:
            inserted=self.catalog.ingest_if_absent_batch(batch,ingested_at_ns=ingested_at_ns)
        latency=(monotonic_ns()-started)/1_000_000.
        with self._lock:self._batches+=1;self._seen+=len(batch);self._inserted+=inserted;self._latency+=latency;self._max_latency=max(self._max_latency,latency)
        return inserted
    def checkpoint(self,*,mode="PASSIVE"):return self.catalog.checkpoint(mode=mode)
    def prune_shards_older_than(self,*,retention_days:int,today=None):return self.catalog.prune_shards_older_than(retention_days=retention_days,today=today)
    def stats(self):
        with self._lock:return {"batches":self._batches,"records_seen":self._seen,"records_inserted":self._inserted,"duplicates":max(0,self._seen-self._inserted),"write_latency_ms":self._latency/self._batches if self._batches else 0.,"max_write_latency_ms":self._max_latency}
    def close(self):self.catalog.close()
