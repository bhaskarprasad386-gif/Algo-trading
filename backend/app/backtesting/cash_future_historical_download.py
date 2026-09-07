"""End-to-end durable Cash-Future historical download orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .angelone_historical import AngelOneHistoricalSource
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_download_status import HistoricalDownloadStatusStore, DownloadChunkStatus
from .historical_ingest import HistoricalIngestionService, HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan, build_chunked_plan
from .nse_session_calendars import nse_session_windows
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow

_TIMEFRAME_INTERVAL_NS = {"1m": 60 * 1_000_000_000, "3m": 3 * 60 * 1_000_000_000, "5m": 5 * 60 * 1_000_000_000, "10m": 10 * 60 * 1_000_000_000, "15m": 15 * 60 * 1_000_000_000, "30m": 30 * 60 * 1_000_000_000, "1h": 60 * 60 * 1_000_000_000, "1d": 24 * 60 * 60 * 1_000_000_000}
_INTRADAY_TIMEFRAMES = frozenset(_TIMEFRAME_INTERVAL_NS) - {"1d"}


def _default_session_windows(request: object) -> tuple[SessionWindow, ...]:
    if request.timeframe not in _INTRADAY_TIMEFRAMES:
        return ()
    return tuple(nse_session_windows(request))


def _clip_sessions(sessions: Iterable[SessionWindow], start_ns: int, end_ns: int) -> tuple[SessionWindow, ...]:
    return tuple(SessionWindow(max(s.start_ns, start_ns), min(s.end_ns, end_ns)) for s in sessions if max(s.start_ns, start_ns) <= min(s.end_ns, end_ns))


@dataclass(frozen=True)
class CashFutureHistoricalDownloadReport:
    queue: CashFutureDownloadQueue | None
    spot_execution: DownloadExecutionResult
    future_executions: tuple[DownloadExecutionResult, ...]
    catalog_count: int
    @property
    def completed(self) -> bool:
        return self.spot_execution.failed_request_index is None and all(r.failed_request_index is None for r in self.future_executions)
    @property
    def completed_chunks(self) -> int:
        return self.spot_execution.completed_chunks + sum(r.completed_chunks for r in self.future_executions)
    @property
    def skipped_chunks(self) -> int:
        return self.spot_execution.skipped_chunks + sum(r.skipped_chunks for r in self.future_executions)
    @property
    def processed_chunks(self) -> int:
        return self.completed_chunks + self.skipped_chunks


class CashFutureHistoricalDownloadService:
    """Download spot/future requests sequentially and persist each chunk immediately."""
    def __init__(self, catalog: HistoricalCatalog, contract_catalog, *, source=None, executor=None, session_windows: Callable[[object], Iterable[SessionWindow]] | None = None, status_store: HistoricalDownloadStatusStore | None = None) -> None:
        self.catalog, self.contract_catalog = catalog, contract_catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion)
        self.completeness = SessionChunkCompleteness(catalog)
        self.session_windows = session_windows or _default_session_windows
        self.status_store = status_store
    @property
    def source_name(self) -> str:
        value = getattr(self.source, "source_name", None) or self.source.__class__.__name__.lower()
        value = str(value).strip()
        if not value: raise ValueError("historical source provider identity cannot be empty")
        return value
    @staticmethod
    def _plan_for_request(request) -> HistoricalSyncPlan:
        return build_chunked_plan(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns, chunk_ns=7 * 24 * 60 * 60 * 1_000_000_000)
    def _chunk_is_complete(self, request, result=None) -> bool:
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe)
        if interval_ns is None: raise ValueError(f"unsupported timeframe for completeness checks: {request.timeframe}")
        sessions = _clip_sessions(self.session_windows(request), request.start_ns, request.end_ns)
        if not sessions:
            watermark = self.catalog.watermark(source=request.source, instrument=request.instrument, timeframe=request.timeframe)
            return watermark is not None and watermark >= request.end_ns
        return self.completeness.is_complete(source=request.source, instrument=request.instrument, timeframe=request.timeframe, interval_ns=interval_ns, chunk=SessionChunk(request.start_ns, request.end_ns), sessions=sessions)
    def _chunk_metrics(self, request):
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe, 0)
        if not interval_ns: return 0,0,0,None
        sessions = _clip_sessions(self.session_windows(request), request.start_ns, request.end_ns)
        if not sessions: return 0,0,0,None
        expected = self.completeness.expected_timestamps(sessions, interval_ns)
        actual = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected), end_ns=max(expected))) if expected else set()
        missing = expected - actual
        return len(expected), len(actual), len(missing), min(missing) if missing else None
    def _refresh_job_progress(self, job_id):
        if self.status_store is None: return
        chunks = self.status_store.chunks(job_id)
        self.status_store.update_job(job_id, completed_chunks=sum(c.status=="COMPLETE" and c.missing_timestamps==0 for c in chunks), skipped_chunks=sum(c.status=="SKIPPED" and c.missing_timestamps==0 for c in chunks), failed_chunks=sum(c.status=="FAILED" or c.missing_timestamps>0 for c in chunks), catalog_count=self.catalog.count(), fetched_records=sum(c.fetched_records for c in chunks), inserted_records=sum(c.inserted_records for c in chunks))
    def _callbacks(self, job_id, sequence_offset=0, sequence_numbers=None):
        if self.status_store is None: return {}
        from .download_status_progress import persist_chunk_result, persist_chunk_start
        def seq(i): return sequence_numbers[i] if sequence_numbers is not None else sequence_offset+i
        def start(i,r,a): persist_chunk_start(self.status_store, job_id=job_id, sequence=seq(i), instrument=r.instrument, start_ns=r.start_ns, end_ns=r.end_ns, attempts=a); self._refresh_job_progress(job_id)
        def skip(i,r):
            e,a,m,f=self._chunk_metrics(r); persist_chunk_result(self.status_store, job_id=job_id, sequence=seq(i), instrument=r.instrument, start_ns=r.start_ns, end_ns=r.end_ns, attempts=0, status="SKIPPED", expected_timestamps=e, actual_timestamps=a, missing_timestamps=m, first_missing_ns=f, catalog_count=self.catalog.count()); self._refresh_job_progress(job_id)
        def complete(i,r,res,a):
            e,ac,m,f=self._chunk_metrics(r); persist_chunk_result(self.status_store, job_id=job_id, sequence=seq(i), instrument=r.instrument, start_ns=r.start_ns, end_ns=r.end_ns, attempts=a, status="COMPLETE", expected_timestamps=e, actual_timestamps=ac, missing_timestamps=m, first_missing_ns=f, fetched_records=res.fetched, inserted_records=res.inserted, catalog_count=self.catalog.count()); self._refresh_job_progress(job_id)
        def failed(i,r,err,a):
            e,ac,m,f=self._chunk_metrics(r); persist_chunk_result(self.status_store, job_id=job_id, sequence=seq(i), instrument=r.instrument, start_ns=r.start_ns, end_ns=r.end_ns, attempts=a, status="FAILED", expected_timestamps=e, actual_timestamps=ac, missing_timestamps=m, first_missing_ns=f, error=str(err), catalog_count=self.catalog.count()); self._refresh_job_progress(job_id); self.status_store.update_job(job_id,status="FAILED",error=str(err))
        return {"on_chunk_start":start,"on_chunk_skip":skip,"on_chunk_complete":complete,"on_chunk_failed":failed}
    def _register_plan(self, job_id, plan, offset):
        if self.status_store is None: return
        for i,r in enumerate(plan.requests): self.status_store.upsert_chunk(DownloadChunkStatus(job_id,offset+i,r.instrument,r.start_ns,r.end_ns,"QUEUED",0))
    def _resume_plan(self, job_id):
        if self.status_store is None: raise ValueError("resume requires a durable status store")
        job=self.status_store.job(job_id)
        if job is None: raise KeyError(job_id)
        if job.source != self.source_name: raise ValueError(f"historical download provider mismatch: job={job.source!r}, source={self.source_name!r}")
        chunks=self.status_store.incomplete_chunks(job_id)
        return HistoricalSyncPlan(tuple(HistoricalFetchRequest(self.source_name,c.instrument,job.timeframe,c.start_ns,c.end_ns) for c in chunks)), tuple(c.sequence for c in chunks)
    def run(self, *, spot_instrument, exchange, underlying, start, end, timeframe="1m", mode="BOTH", retry_attempts=3, should_skip=None, session_windows=None, job_id=None, resume=False):
        original=self.session_windows
        if session_windows is not None: self.session_windows=session_windows
        try:
            if resume:
                if self.status_store is None or job_id is None: raise ValueError("resume requires job_id and durable status store")
                plan, seqs=self._resume_plan(job_id)
                if not plan.requests:
                    self.status_store.update_job(job_id,status="COMPLETE",error=None,catalog_count=self.catalog.count()); empty=DownloadExecutionResult(tuple(),None,tuple()); return CashFutureHistoricalDownloadReport(None,empty,tuple(),self.catalog.count())
                self.status_store.update_job(job_id,status="RUNNING",error=None)
                result=self.executor.run(self.source,plan,retry_attempts=retry_attempts,should_skip=should_skip or self._chunk_is_complete,should_accept=self._chunk_is_complete,**self._callbacks(job_id,sequence_numbers=seqs))
                if result.failed_request_index is None: self.status_store.update_job(job_id,status="COMPLETE",error=None,catalog_count=self.catalog.count())
                return CashFutureHistoricalDownloadReport(None,result,tuple(),self.catalog.count())
            queue=build_rollover_download_queue(catalog=self.contract_catalog,spot_instrument=spot_instrument,exchange=exchange,underlying=underlying,start=start,end=end,timeframe=timeframe,mode=mode,source=self.source_name)
            spot_plan=self._plan_for_request(queue.spot); future_plans=tuple(self._plan_for_request(x.request) for x in queue.futures)
            if self.status_store is not None and job_id is not None:
                total=len(spot_plan.requests)+sum(len(p.requests) for p in future_plans); self.status_store.create_job(job_id=job_id,mode=mode,timeframe=timeframe,spot_instrument=spot_instrument,exchange=exchange,underlying=underlying,start_ns=queue.spot.start_ns,end_ns=queue.spot.end_ns,requested_chunks=total,source=self.source_name,reset_existing=True)
                off=0; self._register_plan(job_id,spot_plan,off); off+=len(spot_plan.requests)
                for p in future_plans: self._register_plan(job_id,p,off); off+=len(p.requests)
            skip=should_skip or self._chunk_is_complete; off=0
            spot=self.executor.run(self.source,spot_plan,retry_attempts=retry_attempts,should_skip=skip,should_accept=self._chunk_is_complete,**(self._callbacks(job_id,off) if job_id else {})); off+=len(spot_plan.requests)
            if spot.failed_request_index is not None: return CashFutureHistoricalDownloadReport(queue,spot,tuple(),self.catalog.count())
            futures=[]
            for p in future_plans:
                r=self.executor.run(self.source,p,retry_attempts=retry_attempts,should_skip=skip,should_accept=self._chunk_is_complete,**(self._callbacks(job_id,off) if job_id else {})); futures.append(r); off+=len(p.requests)
                if r.failed_request_index is not None: break
            return CashFutureHistoricalDownloadReport(queue,spot,tuple(futures),self.catalog.count())
        finally: self.session_windows=original
