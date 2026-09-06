"""End-to-end durable Cash-Future historical download orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .angelone_historical import AngelOneHistoricalSource
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_download_status import HistoricalDownloadStatusStore
from .historical_ingest import HistoricalIngestionService
from .historical_ingest import HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan, build_chunked_plan
from .nse_session_calendars import nse_session_windows
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow

_TIMEFRAME_INTERVAL_NS = {
    "1m": 60 * 1_000_000_000, "3m": 3 * 60 * 1_000_000_000, "5m": 5 * 60 * 1_000_000_000,
    "10m": 10 * 60 * 1_000_000_000, "15m": 15 * 60 * 1_000_000_000, "30m": 30 * 60 * 1_000_000_000,
    "1h": 60 * 60 * 1_000_000_000, "1d": 24 * 60 * 60 * 1_000_000_000,
}
_INTRADAY_TIMEFRAMES = frozenset(_TIMEFRAME_INTERVAL_NS) - {"1d"}


def _default_session_windows(request: object) -> tuple[SessionWindow, ...]:
    if request.timeframe not in _INTRADAY_TIMEFRAMES:
        return ()
    return tuple(nse_session_windows(request))


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

    def __init__(self, catalog: HistoricalCatalog, contract_catalog, *, source=None, executor=None,
                 session_windows: Callable[[object], Iterable[SessionWindow]] | None = None,
                 status_store: HistoricalDownloadStatusStore | None = None) -> None:
        self.catalog = catalog
        self.contract_catalog = contract_catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion)
        self.completeness = SessionChunkCompleteness(catalog)
        self.session_windows = session_windows or _default_session_windows
        self.status_store = status_store

    @staticmethod
    def _plan_for_request(request) -> HistoricalSyncPlan:
        return build_chunked_plan(source=request.source, instrument=request.instrument, timeframe=request.timeframe,
                                  start_ns=request.start_ns, end_ns=request.end_ns,
                                  chunk_ns=7 * 24 * 60 * 60 * 1_000_000_000)

    def _chunk_is_complete(self, request) -> bool:
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe)
        if interval_ns is None:
            raise ValueError(f"unsupported timeframe for completeness checks: {request.timeframe}")
        sessions = tuple(self.session_windows(request))
        if not sessions:
            return True
        return self.completeness.is_complete(source=request.source, instrument=request.instrument, timeframe=request.timeframe,
                                             interval_ns=interval_ns, chunk=SessionChunk(request.start_ns, request.end_ns), sessions=sessions)

    def _chunk_metrics(self, request) -> tuple[int, int, int, int | None]:
        sessions = tuple(self.session_windows(request))
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe, 0)
        if not sessions or not interval_ns:
            return 0, 0, 0, None
        expected_set = self.completeness._expected_timestamps(sessions, interval_ns)
        if not expected_set:
            return 0, 0, 0, None
        actual_set = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe,
                                                  start_ns=min(expected_set), end_ns=max(expected_set)))
        missing_set = expected_set - actual_set
        return len(expected_set), len(actual_set), len(missing_set), min(missing_set) if missing_set else None

    def _callbacks(self, job_id: str, sequence_offset: int):
        store = self.status_store
        if store is None:
            return {}

        def start(index, request, attempt):
            from .download_status_progress import persist_chunk_start
            persist_chunk_start(store, job_id=job_id, sequence=sequence_offset + index, instrument=request.instrument,
                                start_ns=request.start_ns, end_ns=request.end_ns, attempts=attempt)

        def skip(index, request):
            from .download_status_progress import persist_chunk_result
            expected, actual, missing, first_missing = self._chunk_metrics(request)
            persist_chunk_result(store, job_id=job_id, sequence=sequence_offset + index, instrument=request.instrument,
                                 start_ns=request.start_ns, end_ns=request.end_ns, attempts=0, status="SKIPPED",
                                 expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing,
                                 first_missing_ns=first_missing)

        def complete(index, request, result, attempt):
            from .download_status_progress import persist_chunk_result
            expected, actual, missing, first_missing = self._chunk_metrics(request)
            persist_chunk_result(store, job_id=job_id, sequence=sequence_offset + index, instrument=request.instrument,
                                 start_ns=request.start_ns, end_ns=request.end_ns, attempts=attempt, status="COMPLETE",
                                 expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing,
                                 first_missing_ns=first_missing, fetched_records=result.fetched, inserted_records=result.inserted)

        def failed(index, request, error, attempts):
            from .download_status_progress import persist_chunk_result
            expected, actual, missing, first_missing = self._chunk_metrics(request)
            persist_chunk_result(store, job_id=job_id, sequence=sequence_offset + index, instrument=request.instrument,
                                 start_ns=request.start_ns, end_ns=request.end_ns, attempts=attempts, status="FAILED",
                                 expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing,
                                 first_missing_ns=first_missing, error=str(error))
            store.update_job(job_id, status="FAILED", error=str(error))

        return {"on_chunk_start": start, "on_chunk_skip": skip, "on_chunk_complete": complete, "on_chunk_failed": failed}

    def _resume_plan(self, job_id: str) -> HistoricalSyncPlan:
        if self.status_store is None:
            raise ValueError("resume requires a durable status store")
        chunks = self.status_store.incomplete_chunks(job_id)
        requests = tuple(
            HistoricalFetchRequest("angelone", chunk.instrument, self.status_store.job(job_id).timeframe, chunk.start_ns, chunk.end_ns)
            for chunk in chunks
        )
        return HistoricalSyncPlan(requests)

    def run(self, *, spot_instrument: str, exchange: str, underlying: str, start, end,
            timeframe: str = "1m", mode: str = "BOTH", retry_attempts: int = 3,
            should_skip: Callable[[object], bool] | None = None, job_id: str | None = None,
            resume: bool = False) -> CashFutureHistoricalDownloadReport:
        if resume:
            if self.status_store is None or job_id is None:
                raise ValueError("resume requires job_id and durable status store")
            job = self.status_store.job(job_id)
            if job is None:
                raise KeyError(job_id)
            chunks = self.status_store.incomplete_chunks(job_id)
            if not chunks:
                self.status_store.update_job(job_id, status="COMPLETE", error=None, catalog_count=self.catalog.count())
                empty = DownloadExecutionResult(tuple(), None, tuple())
                return CashFutureHistoricalDownloadReport(None, empty, tuple(), self.catalog.count())
            plan = self._resume_plan(job_id)
            self.status_store.update_job(job_id, status="RUNNING", error=None)
            callbacks = self._callbacks(job_id, 0)
            result = self.executor.run(
                self.source, plan, retry_attempts=retry_attempts,
                should_skip=should_skip or self._chunk_is_complete,
                should_accept=self._chunk_is_complete, **callbacks,
            )
            if result.failed_request_index is None:
                self.status_store.update_job(job_id, status="COMPLETE", catalog_count=self.catalog.count())
            return CashFutureHistoricalDownloadReport(None, result, tuple(), self.catalog.count())

        queue = build_rollover_download_queue(catalog=self.contract_catalog, spot_instrument=spot_instrument, exchange=exchange,
                                               underlying=underlying, start=start, end=end, timeframe=timeframe, mode=mode)
        spot_plan = self._plan_for_request(queue.spot)
        future_plans = tuple(self._plan_for_request(item.request) for item in queue.futures)
        total_chunks = len(spot_plan.requests) + sum(len(plan.requests) for plan in future_plans)
        if self.status_store is not None and job_id is not None:
            start_ns = min([queue.spot.start_ns, *[item.request.start_ns for item in queue.futures]])
            end_ns = max([queue.spot.end_ns, *[item.request.end_ns for item in queue.futures]])
            self.status_store.create_job(job_id=job_id, mode=mode, timeframe=timeframe, spot_instrument=spot_instrument,
                                         exchange=exchange, underlying=underlying, start_ns=start_ns, end_ns=end_ns,
                                         requested_chunks=total_chunks, reset_existing=True)
        effective_skip = should_skip or self._chunk_is_complete
        sequence_offset = 0
        callbacks = self._callbacks(job_id, sequence_offset) if job_id else {}
        spot_result = self.executor.run(self.source, spot_plan, retry_attempts=retry_attempts,
                                        should_skip=effective_skip, should_accept=self._chunk_is_complete, **callbacks)
        sequence_offset += len(spot_plan.requests)
        if spot_result.failed_request_index is not None:
            return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(), self.catalog.count())
        future_results = []
        for item, plan in zip(queue.futures, future_plans):
            callbacks = self._callbacks(job_id, sequence_offset) if job_id else {}
            result = self.executor.run(self.source, plan, retry_attempts=retry_attempts,
                                       should_skip=effective_skip, should_accept=self._chunk_is_complete, **callbacks)
            future_results.append(result)
            sequence_offset += len(plan.requests)
            if result.failed_request_index is not None:
                return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(future_results), self.catalog.count())
        if self.status_store is not None and job_id is not None:
            completed = spot_result.completed_chunks + sum(r.completed_chunks for r in future_results)
            skipped = spot_result.skipped_chunks + sum(r.skipped_chunks for r in future_results)
            self.status_store.update_job(job_id, status="COMPLETE", completed_chunks=completed, skipped_chunks=skipped,
                                         failed_chunks=0, catalog_count=self.catalog.count())
        return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(future_results), self.catalog.count())
