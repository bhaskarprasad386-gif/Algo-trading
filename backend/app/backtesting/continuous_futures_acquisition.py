"""Historical acquisition orchestration for continuous futures chains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from .continuous_futures import build_continuous_futures_series_from_catalog
from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalSource
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan, build_chunked_plan, build_session_gap_plan
from .trading_calendar import TradingCalendar


@dataclass(frozen=True)
class ContinuousFuturesAcquisitionReport:
    windows: tuple[FNORolloverWindow, ...]
    plan: HistoricalSyncPlan
    execution: DownloadExecutionResult

    @property
    def completed(self) -> bool:
        return self.execution.failed_request_index is None


def _window_sessions(window: FNORolloverWindow, calendar: TradingCalendar) -> tuple[tuple[int, int], ...]:
    return tuple((session.start_ns, session.end_ns) for session in calendar.sessions_between(window.start_date, window.end_date))


def build_continuous_futures_acquisition_plan(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build stable provider requests only inside active-contract trading sessions."""
    if not source.strip(): raise ValueError("source is required")
    if not timeframe.strip(): raise ValueError("timeframe is required")
    if interval_ns <= 0: raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0: raise ValueError("max_request_ns must be positive")
    requests: list[HistoricalFetchRequest] = []
    for window in windows:
        for session_start, session_end in _window_sessions(window, calendar):
            requests.extend(build_chunked_plan(source=source, instrument=f"NFO:{window.contract_token}", timeframe=timeframe, start_ns=session_start, end_ns=session_end, chunk_ns=max_request_ns).requests)
    return HistoricalSyncPlan(tuple(requests))


def build_continuous_futures_gap_plan(
    catalog: HistoricalCatalog,
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build repair-only requests from catalog session gaps for active contracts."""
    if not source.strip(): raise ValueError("source is required")
    if not timeframe.strip(): raise ValueError("timeframe is required")
    if interval_ns <= 0: raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0: raise ValueError("max_request_ns must be positive")
    requests: list[HistoricalFetchRequest] = []
    for window in windows:
        requests.extend(build_session_gap_plan(catalog, calendar, source=source, instrument=f"NFO:{window.contract_token}", timeframe=timeframe, interval_ns=interval_ns, start_date=window.start_date, end_date=window.end_date, max_request_ns=max_request_ns).requests)
    return HistoricalSyncPlan(tuple(requests))


def acquire_continuous_futures_history(
    catalog: HistoricalCatalog, source: HistoricalSource, windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source_name: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None, job_store: HistoricalJobStore | None = None,
    job_id: str | None = None, run_id: str | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Fill missing chain/session chunks with optional durable restart state."""
    windows = tuple(windows)
    plan = build_continuous_futures_acquisition_plan(windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    runner = executor or ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)

    def complete(request: HistoricalFetchRequest) -> bool:
        expected = range(request.start_ns, request.end_ns + 1, interval_ns)
        if not expected: return True
        present = set(catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns))
        return all(timestamp in present for timestamp in expected)

    if job_store is None:
        if job_id is not None or run_id is not None: raise ValueError("job_id and run_id require job_store")
        execution = runner.run(source, plan, should_skip=complete)
    else:
        if not job_id or not run_id: raise ValueError("job_store requires both job_id and run_id")
        execution = runner.run_durable(source, plan, job_store=job_store, job_id=job_id, run_id=run_id, should_skip=complete)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


def _day_start_ns(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _day_end_ns(value: date) -> int:
    return int(datetime.combine(value, time.max, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


__all__ = ["ContinuousFuturesAcquisitionReport", "build_continuous_futures_acquisition_plan", "build_continuous_futures_gap_plan", "acquire_continuous_futures_history", "build_continuous_futures_series_from_catalog"]
