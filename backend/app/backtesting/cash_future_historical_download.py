"""End-to-end durable Cash-Future historical download orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from app.market_data.instruments import InstrumentMaster

from .angelone_historical import AngelOneHistoricalSource
from .cash_future_contract_preflight import CashFutureContractPreflight
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore
from .historical_ingest import HistoricalIngestionService, HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan, build_chunked_plan
from .nse_session_calendars import nse_daily_timestamps, nse_session_windows
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow

MARKET_TZ = ZoneInfo("Asia/Kolkata")
_TIMEFRAME_INTERVAL_NS = {"1m": 60*1_000_000_000, "3m": 3*60*1_000_000_000, "5m": 5*60*1_000_000_000, "10m": 10*60*1_000_000_000, "15m": 15*60*1_000_000_000, "30m": 30*60*1_000_000_000, "1h": 60*60*1_000_000_000, "1d": 24*60*60*1_000_000_000}
_INTRADAY_TIMEFRAMES = frozenset(_TIMEFRAME_INTERVAL_NS) - {"1d"}


def _default_session_windows(request: object) -> tuple[SessionWindow, ...]:
    if request.timeframe not in _INTRADAY_TIMEFRAMES:
        return ()
    return tuple(nse_session_windows(request))


def _normalise_cash_instrument(master: InstrumentMaster, value: str, exchange: str) -> str:
    """Return an Angel One-compatible cash instrument, resolving plain symbols safely."""
    instrument = str(value).strip()
    requested_exchange = str(exchange).strip().upper()
    if not instrument:
        raise ValueError("cash instrument cannot be empty")
    if not requested_exchange:
        raise ValueError("cash exchange cannot be empty")
    if ":" in instrument:
        parts = instrument.split(":")
        if len(parts) not in (2, 3) or not all(part.strip() for part in parts):
            raise ValueError(f"invalid cash instrument format: {instrument!r}")
        if parts[0].strip().upper() != requested_exchange:
            raise ValueError(
                f"cash instrument exchange mismatch: requested={requested_exchange}, instrument={parts[0].strip().upper()}"
            )
        return instrument
    resolved = master.resolve_cash_instrument(instrument, exchange="NSE")
    token = str(resolved["token"]).strip()
    symbol = str(resolved.get("symbol") or instrument).strip()
    resolved_exchange = str(resolved.get("exch_seg") or "NSE").strip().upper()
    if resolved_exchange != "NSE":
        raise LookupError(f"resolved cash instrument is not NSE: {resolved_exchange}:{symbol}")
    return f"NSE:{token}:{symbol}"


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

    def __init__(self, catalog: HistoricalCatalog, contract_catalog, *, source=None, executor=None, session_windows: Callable[[object], Iterable[SessionWindow]] | None = None, status_store: HistoricalDownloadStatusStore | None = None, instrument_master: InstrumentMaster | None = None) -> None:
        self.catalog = catalog
        self.contract_catalog = contract_catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion, collect_results=False)
        self.completeness = SessionChunkCompleteness(catalog)
        self.session_windows = session_windows or _default_session_windows
        self.status_store = status_store
        self.instrument_master = instrument_master or InstrumentMaster()
        self.contract_preflight = CashFutureContractPreflight(contract_catalog)

    @property
    def source_name(self) -> str:
        value = getattr(self.source, "source_name", None) or self.source.__class__.__name__.lower()
        value = str(value).strip()
        if not value:
            raise ValueError("historical source provider identity cannot be empty")
        return value

    @staticmethod
    def _plan_for_request(request) -> HistoricalSyncPlan:
        return build_chunked_plan(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns, chunk_ns=7*24*60*60*1_000_000_000)

    def _chunk_is_complete(self, request, result=None) -> bool:
        if request.timeframe == "1d":
            expected = set(nse_daily_timestamps(request))
            if not expected:
                return True
            actual = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected), end_ns=max(expected)))
            return expected.issubset(actual)
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe)
        if interval_ns is None:
            raise ValueError(f"unsupported timeframe for completeness checks: {request.timeframe}")
        sessions = tuple(self.session_windows(request))
        if not sessions:
            watermark = self.catalog.watermark(source=request.source, instrument=request.instrument, timeframe=request.timeframe)
            return watermark is not None and watermark >= request.end_ns
        return self.completeness.is_complete(source=request.source, instrument=request.instrument, timeframe=request.timeframe, interval_ns=interval_ns, chunk=SessionChunk(request.start_ns, request.end_ns), sessions=sessions)

    def _chunk_metrics(self, request) -> tuple[int, int, int, int | None]:
        if request.timeframe == "1d":
            expected_set = set(nse_daily_timestamps(request))
            if not expected_set: return 0, 0, 0, None
            actual_set = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected_set), end_ns=max(expected_set)))
            missing_set = expected_set - actual_set
            return len(expected_set), len(actual_set), len(missing_set), min(missing_set) if missing_set else None
        sessions = tuple(self.session_windows(request)); interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe, 0)
        if not sessions or not interval_ns: return 0, 0, 0, None
        expected_set = self.completeness.expected_timestamps(sessions, interval_ns)
        if not expected_set: return 0, 0, 0, None
        actual_set = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected_set), end_ns=max(expected_set)))
        missing_set = expected_set - actual_set
        return len(expected_set), len(actual_set), len(missing_set), min(missing_set) if missing_set else None

    @staticmethod
    def _missing_runs(missing: set[int], interval_ns: int) -> tuple[tuple[int, int], ...]:
        """Collapse exact missing timestamps into minimal contiguous fetch ranges."""
        if not missing:
            return ()
        ordered = sorted(missing)
        runs: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for timestamp in ordered[1:]:
            if timestamp == previous + interval_ns:
                previous = timestamp
                continue
            runs.append((start, previous))
            start = previous = timestamp
        runs.append((start, previous))
        return tuple(runs)

    def _missing_requests_for_chunk(self, job, chunk) -> tuple[HistoricalFetchRequest, ...]:
        request = HistoricalFetchRequest(self.source_name, chunk.instrument, job.timeframe, chunk.start_ns, chunk.end_ns)
        if job.timeframe == "1d":
            expected = set(nse_daily_timestamps(request))
            actual = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected), end_ns=max(expected))) if expected else set()
            runs = self._missing_runs(expected - actual, _TIMEFRAME_INTERVAL_NS["1d"])
        else:
            interval_ns = _TIMEFRAME_INTERVAL_NS[job.timeframe]
            sessions = tuple(self.session_windows(request))
            expected = self.completeness.expected_timestamps(sessions, interval_ns)
            actual = set(self.catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=min(expected), end_ns=max(expected))) if expected else set()
            runs = self._missing_runs(expected - actual, interval_ns)
        return tuple(HistoricalFetchRequest(self.source_name, chunk.instrument, job.timeframe, start_ns, end_ns) for start_ns, end_ns in runs)

    def _register_plan(self, job_id: str, plans: tuple[HistoricalSyncPlan, ...]) -> None:
        if self.status_store is None: return
        sequence = 0
        for plan in plans:
            for request in plan.requests:
                expected, actual, missing, first_missing = self._chunk_metrics(request)
                self.status_store.upsert_chunk(DownloadChunkStatus(job_id=job_id, sequence=sequence, instrument=request.instrument, start_ns=request.start_ns, end_ns=request.end_ns, status="QUEUED", attempts=0, expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing, first_missing_ns=first_missing))
                sequence += 1

    def _callbacks(self, job_id: str, sequence_offset: int = 0, sequence_numbers: tuple[int, ...] | None = None):
        store = self.status_store
        if store is None: return {}
        def status_sequence(index: int) -> int: return sequence_numbers[index] if sequence_numbers is not None else sequence_offset + index
        def status_request(index, request):
            if sequence_numbers is None:
                return request
            sequence = status_sequence(index)
            parent = next((chunk for chunk in store.chunks(job_id) if chunk.sequence == sequence), None)
            if parent is None:
                raise KeyError(f"download chunk sequence not found: {sequence}")
            return HistoricalFetchRequest(self.source_name, parent.instrument, request.timeframe, parent.start_ns, parent.end_ns)
        def start(index, request, attempt):
            from .download_status_progress import persist_chunk_start
            target = status_request(index, request)
            persist_chunk_start(store, job_id=job_id, sequence=status_sequence(index), instrument=target.instrument, start_ns=target.start_ns, end_ns=target.end_ns, attempts=attempt)
        def skip(index, request):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            persist_chunk_result(store, job_id=job_id, sequence=status_sequence(index), instrument=target.instrument, start_ns=target.start_ns, end_ns=target.end_ns, attempts=0, status="SKIPPED", expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing, first_missing_ns=first_missing)
        def complete(index, request, result, attempt):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            existing = next((chunk for chunk in store.chunks(job_id) if chunk.sequence == status_sequence(index)), None)
            fetched = (existing.fetched_records if existing else 0) + result.fetched
            inserted = (existing.inserted_records if existing else 0) + result.inserted
            persist_chunk_result(store, job_id=job_id, sequence=status_sequence(index), instrument=target.instrument, start_ns=target.start_ns, end_ns=target.end_ns, attempts=attempt, status="COMPLETE" if missing == 0 else "RUNNING", expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing, first_missing_ns=first_missing, fetched_records=fetched, inserted_records=inserted)
        def failed(index, request, error, attempts):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            persist_chunk_result(store, job_id=job_id, sequence=status_sequence(index), instrument=target.instrument, start_ns=target.start_ns, end_ns=target.end_ns, attempts=attempts, status="FAILED", expected_timestamps=expected, actual_timestamps=actual, missing_timestamps=missing, first_missing_ns=first_missing, error=str(error))
            store.update_job(job_id, status="FAILED", error=str(error))
        return {"on_chunk_start": start, "on_chunk_skip": skip, "on_chunk_complete": complete, "on_chunk_failed": failed}

    def _resume_plan(self, job_id: str) -> tuple[HistoricalSyncPlan, tuple[int, ...]]:
        if self.status_store is None: raise ValueError("resume requires a durable status store")
        job = self.status_store.job(job_id)
        if job is None: raise KeyError(job_id)
        if job.source != self.source_name:
            raise ValueError(f"historical download provider mismatch: job={job.source!r}, source={self.source_name!r}")
        chunks = self.status_store.incomplete_chunks(job_id)
        requests: list[HistoricalFetchRequest] = []
        sequences: list[int] = []
        for chunk in chunks:
            missing_requests = self._missing_requests_for_chunk(job, chunk)
            for request in missing_requests:
                requests.append(request)
                sequences.append(chunk.sequence)
        return HistoricalSyncPlan(tuple(requests)), tuple(sequences)

    def run(self, *, spot_instrument: str, exchange: str, underlying: str, start, end, timeframe: str = "1m", mode: str = "BOTH", retry_attempts: int = 3, should_skip: Callable[[object], bool] | None = None, session_windows: Callable[[object], Iterable[SessionWindow]] | None = None, job_id: str | None = None, resume: bool = False) -> CashFutureHistoricalDownloadReport:
        original_session_windows = self.session_windows
        if session_windows is not None: self.session_windows = session_windows
        try:
            if resume:
                if self.status_store is None or job_id is None: raise ValueError("resume requires job_id and durable status store")
                plan, sequence_numbers = self._resume_plan(job_id)
                if not plan.requests:
                    self.status_store.update_job(job_id, status="COMPLETE", error=None, catalog_count=self.catalog.count())
                    empty = DownloadExecutionResult(tuple(), None, tuple())
                    return CashFutureHistoricalDownloadReport(None, empty, tuple(), self.catalog.count())
                self.status_store.update_job(job_id, status="RUNNING", error=None)
                result = self.executor.run(self.source, plan, retry_attempts=retry_attempts, should_skip=should_skip or self._chunk_is_complete, should_accept=self._chunk_is_complete, **self._callbacks(job_id, sequence_numbers=sequence_numbers))
                if result.failed_request_index is None:
                    status = "COMPLETE" if not self.status_store.incomplete_chunks(job_id) else "RUNNING"
                    self.status_store.update_job(job_id, status=status, catalog_count=self.catalog.count())
                return CashFutureHistoricalDownloadReport(None, result, tuple(), self.catalog.count())

            start_date = start.astimezone(MARKET_TZ).date() if getattr(start, "tzinfo", None) else start.date()
            end_date = end.astimezone(MARKET_TZ).date() if getattr(end, "tzinfo", None) else end.date()
            self.contract_preflight.require_complete(exchange=exchange, underlying=underlying, start=start_date, end=end_date, mode=mode)
            resolved_spot_instrument = _normalise_cash_instrument(self.instrument_master, spot_instrument, "NSE")
            queue = build_rollover_download_queue(catalog=self.contract_catalog, spot_instrument=resolved_spot_instrument, exchange=exchange, underlying=underlying, start=start, end=end, timeframe=timeframe, mode=mode, source=self.source_name)
            spot_plan = self._plan_for_request(queue.spot)
            future_plans = tuple(self._plan_for_request(item.request) for item in queue.futures)
            all_plans = (spot_plan, *future_plans)
            total_chunks = sum(len(plan.requests) for plan in all_plans)
            if self.status_store is not None and job_id is not None:
                start_ns = min([queue.spot.start_ns, *[item.request.start_ns for item in queue.futures]])
                end_ns = max([queue.spot.end_ns, *[item.request.end_ns for item in queue.futures]])
                self.status_store.create_job(job_id=job_id, mode=mode, timeframe=timeframe, spot_instrument=resolved_spot_instrument, exchange=exchange, underlying=underlying, start_ns=start_ns, end_ns=end_ns, requested_chunks=total_chunks, source=self.source_name, reset_existing=True)
                self._register_plan(job_id, all_plans)
            effective_skip = should_skip or self._chunk_is_complete; sequence_offset = 0
            spot_result = self.executor.run(self.source, spot_plan, retry_attempts=retry_attempts, should_skip=effective_skip, should_accept=self._chunk_is_complete, **(self._callbacks(job_id, sequence_offset) if job_id else {}))
            sequence_offset += len(spot_plan.requests)
            if spot_result.failed_request_index is not None:
                if self.status_store is not None and job_id is not None:
                    self.status_store.update_job(job_id, status="FAILED", error=f"spot chunk failed at request {spot_result.failed_request_index}")
                return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(), self.catalog.count())
            future_results = []
            for item, plan in zip(queue.futures, future_plans):
                result = self.executor.run(self.source, plan, retry_attempts=retry_attempts, should_skip=effective_skip, should_accept=self._chunk_is_complete, **(self._callbacks(job_id, sequence_offset) if job_id else {}))
                future_results.append(result); sequence_offset += len(plan.requests)
                if result.failed_request_index is not None:
                    if self.status_store is not None and job_id is not None:
                        self.status_store.update_job(job_id, status="FAILED", error=f"future chunk failed at request {result.failed_request_index}")
                    return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(future_results), self.catalog.count())
            if self.status_store is not None and job_id is not None:
                completed = spot_result.completed_chunks + sum(r.completed_chunks for r in future_results); skipped = spot_result.skipped_chunks + sum(r.skipped_chunks for r in future_results)
                self.status_store.update_job(job_id, status="COMPLETE", completed_chunks=completed, skipped_chunks=skipped, failed_chunks=0, catalog_count=self.catalog.count())
            return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(future_results), self.catalog.count())
        finally:
            self.session_windows = original_session_windows
