"""End-to-end durable Cash-Future historical download orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .angelone_historical import AngelOneHistoricalSource
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalIngestionService
from .historical_sync import HistoricalSyncPlan, build_chunked_plan
from .nse_session_calendar import nse_session_windows_for_request
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow


_TIMEFRAME_INTERVAL_NS = {
    "1m": 60 * 1_000_000_000,
    "3m": 3 * 60 * 1_000_000_000,
    "5m": 5 * 60 * 1_000_000_000,
    "10m": 10 * 60 * 1_000_000_000,
    "15m": 15 * 60 * 1_000_000_000,
    "30m": 30 * 60 * 1_000_000_000,
    "1h": 60 * 60 * 1_000_000_000,
    "1d": 24 * 60 * 60 * 1_000_000_000,
}
_INTRADAY_TIMEFRAMES = frozenset(_TIMEFRAME_INTERVAL_NS) - {"1d"}


def _default_session_windows(request: object) -> tuple[SessionWindow, ...]:
    """Use segment-aware versioned NSE calendars for intraday completeness."""
    if request.timeframe not in _INTRADAY_TIMEFRAMES:
        return ()
    return tuple(nse_session_windows_for_request(request))


@dataclass(frozen=True)
class CashFutureHistoricalDownloadReport:
    queue: CashFutureDownloadQueue
    spot_execution: DownloadExecutionResult
    future_executions: tuple[DownloadExecutionResult, ...]
    catalog_count: int

    @property
    def completed(self) -> bool:
        return self.spot_execution.failed_request_index is None and all(
            result.failed_request_index is None for result in self.future_executions
        )

    @property
    def completed_chunks(self) -> int:
        return self.spot_execution.completed_chunks + sum(
            result.completed_chunks for result in self.future_executions
        )

    @property
    def skipped_chunks(self) -> int:
        return self.spot_execution.skipped_chunks + sum(
            result.skipped_chunks for result in self.future_executions
        )

    @property
    def processed_chunks(self) -> int:
        return self.completed_chunks + self.skipped_chunks


class CashFutureHistoricalDownloadService:
    """Download spot/future requests sequentially and persist each chunk immediately."""

    def __init__(
        self,
        catalog: HistoricalCatalog,
        contract_catalog,
        *,
        source=None,
        executor=None,
        session_windows: Callable[[object], Iterable[SessionWindow]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.contract_catalog = contract_catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion)
        self.completeness = SessionChunkCompleteness(catalog)
        self.session_windows = session_windows or _default_session_windows

    @staticmethod
    def _plan_for_request(request) -> HistoricalSyncPlan:
        chunk_ns = 7 * 24 * 60 * 60 * 1_000_000_000
        return build_chunked_plan(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            start_ns=request.start_ns,
            end_ns=request.end_ns,
            chunk_ns=chunk_ns,
        )

    def _should_skip_chunk(self, request) -> bool:
        interval_ns = _TIMEFRAME_INTERVAL_NS.get(request.timeframe)
        if interval_ns is None:
            raise ValueError(f"unsupported timeframe for completeness checks: {request.timeframe}")
        sessions = tuple(self.session_windows(request))
        if not sessions:
            return False
        return self.completeness.is_complete(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            interval_ns=interval_ns,
            chunk=SessionChunk(request.start_ns, request.end_ns),
            sessions=sessions,
        )

    def run(
        self,
        *,
        spot_instrument: str,
        exchange: str,
        underlying: str,
        start,
        end,
        timeframe: str = "1m",
        mode: str = "BOTH",
        retry_attempts: int = 3,
        should_skip: Callable[[object], bool] | None = None,
    ) -> CashFutureHistoricalDownloadReport:
        queue = build_rollover_download_queue(
            catalog=self.contract_catalog,
            spot_instrument=spot_instrument,
            exchange=exchange,
            underlying=underlying,
            start=start,
            end=end,
            timeframe=timeframe,
            mode=mode,
        )
        effective_skip = should_skip or self._should_skip_chunk
        spot_result = self.executor.run(
            self.source,
            self._plan_for_request(queue.spot),
            retry_attempts=retry_attempts,
            should_skip=effective_skip,
        )
        if spot_result.failed_request_index is not None:
            return CashFutureHistoricalDownloadReport(
                queue, spot_result, tuple(), self.catalog.count()
            )

        future_results: list[DownloadExecutionResult] = []
        for item in queue.futures:
            result = self.executor.run(
                self.source,
                self._plan_for_request(item.request),
                retry_attempts=retry_attempts,
                should_skip=effective_skip,
            )
            future_results.append(result)
            if result.failed_request_index is not None:
                break
        return CashFutureHistoricalDownloadReport(
            queue, spot_result, tuple(future_results), self.catalog.count()
        )
