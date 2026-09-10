"""Historical acquisition orchestration for continuous futures chains."""

from __future__ import annotations

from dataclasses import dataclass

from .continuous_futures import build_continuous_futures_series_from_catalog
from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalSource
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan, build_chunked_plan
from .trading_calendar import TradingCalendar


@dataclass(frozen=True)
class ContinuousFuturesAcquisitionReport:
    windows: tuple[FNORolloverWindow, ...]
    plan: HistoricalSyncPlan
    execution: DownloadExecutionResult

    @property
    def completed(self) -> bool:
        return self.execution.failed_request_index is None


def _validate_inputs(*, source: str, timeframe: str, interval_ns: int, max_request_ns: int) -> None:
    if not source.strip():
        raise ValueError("source is required")
    if not timeframe.strip():
        raise ValueError("timeframe is required")
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")


def _window_sessions(window: FNORolloverWindow, calendar: TradingCalendar) -> tuple[tuple[int, int], ...]:
    return tuple((session.start_ns, session.end_ns) for session in calendar.sessions_between(window.start_date, window.end_date))


def build_continuous_futures_acquisition_plan(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build stable provider requests only inside active-contract trading sessions."""
    _validate_inputs(source=source, timeframe=timeframe, interval_ns=interval_ns, max_request_ns=max_request_ns)
    requests: list[HistoricalFetchRequest] = []
    for window in windows:
        for session_start, session_end in _window_sessions(window, calendar):
            requests.extend(build_chunked_plan(source=source, instrument=f"NFO:{window.contract_token}", timeframe=timeframe, start_ns=session_start, end_ns=session_end, chunk_ns=max_request_ns).requests)
    return HistoricalSyncPlan(tuple(requests))


def _missing_ranges(
    catalog: HistoricalCatalog,
    *, source: str, instrument: str, timeframe: str,
    session_start_ns: int, session_end_ns: int, interval_ns: int,
) -> tuple[tuple[int, int], ...]:
    """Return contiguous missing cadence ranges, including leading/trailing/empty sessions."""
    expected = range(session_start_ns, session_end_ns + 1, interval_ns)
    present = set(catalog.timestamps(source=source, instrument=instrument, timeframe=timeframe, start_ns=session_start_ns, end_ns=session_end_ns))
    ranges: list[tuple[int, int]] = []
    range_start: int | None = None
    previous_missing: int | None = None
    for timestamp in expected:
        if timestamp not in present:
            if range_start is None:
                range_start = timestamp
            previous_missing = timestamp
            continue
        if range_start is not None:
            ranges.append((range_start, previous_missing))
            range_start = None
            previous_missing = None
    if range_start is not None:
        ranges.append((range_start, previous_missing))
    return tuple(ranges)


def build_continuous_futures_gap_plan(
    catalog: HistoricalCatalog,
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build repair requests for every missing cadence point inside active sessions.

    Repairs leading, internal, trailing and completely empty sessions without
    crossing session, closed-day or rollover-contract boundaries.
    """
    _validate_inputs(source=source, timeframe=timeframe, interval_ns=interval_ns, max_request_ns=max_request_ns)
    requests: list[HistoricalFetchRequest] = []
    for window in windows:
        instrument = f"NFO:{window.contract_token}"
        for session_start, session_end in _window_sessions(window, calendar):
            for gap_start, gap_end in _missing_ranges(catalog, source=source, instrument=instrument, timeframe=timeframe, session_start_ns=session_start, session_end_ns=session_end, interval_ns=interval_ns):
                requests.extend(build_chunked_plan(source=source, instrument=instrument, timeframe=timeframe, start_ns=gap_start, end_ns=gap_end, chunk_ns=max_request_ns).requests)
    return HistoricalSyncPlan(tuple(requests))


def _complete(catalog: HistoricalCatalog, request: HistoricalFetchRequest, interval_ns: int) -> bool:
    expected = range(request.start_ns, request.end_ns + 1, interval_ns)
    present = set(catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns))
    return all(timestamp in present for timestamp in expected)


def acquire_continuous_futures_history(
    catalog: HistoricalCatalog, source: HistoricalSource, windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source_name: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None, job_store: HistoricalJobStore | None = None,
    job_id: str | None = None, run_id: str | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Acquire the stable full chain plan with optional durable restart state."""
    windows = tuple(windows)
    plan = build_continuous_futures_acquisition_plan(windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    runner = executor or ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)
    should_skip = lambda request: _complete(catalog, request, interval_ns)
    should_accept = lambda request, result: _complete(catalog, request, interval_ns)
    if job_store is None:
        if job_id is not None or run_id is not None:
            raise ValueError("job_id and run_id require job_store")
        execution = runner.run(source, plan, should_skip=should_skip, should_accept=should_accept)
    else:
        if not job_id or not run_id:
            raise ValueError("job_store requires both job_id and run_id")
        execution = runner.run_durable(source, plan, job_store=job_store, job_id=job_id, run_id=run_id, should_skip=should_skip, should_accept=should_accept)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


def repair_continuous_futures_history_gaps(
    catalog: HistoricalCatalog, source: HistoricalSource, windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source_name: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None, job_store: HistoricalJobStore | None = None,
    job_id: str | None = None, run_id: str | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Run a separately fingerprinted, catalog-driven repair job for missing session points.

    The repair plan is frozen when the job starts, so it can be resumed without
    changing the fingerprint of the primary acquisition job.
    """
    windows = tuple(windows)
    plan = build_continuous_futures_gap_plan(catalog, windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    runner = executor or ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)
    should_skip = lambda request: _complete(catalog, request, interval_ns)
    should_accept = lambda request, result: _complete(catalog, request, interval_ns)
    if job_store is None:
        if job_id is not None or run_id is not None:
            raise ValueError("job_id and run_id require job_store")
        execution = runner.run(source, plan, should_skip=should_skip, should_accept=should_accept)
    else:
        if not job_id or not run_id:
            raise ValueError("job_id and run_id require job_store")
        execution = runner.run_durable(source, plan, job_store=job_store, job_id=job_id, run_id=run_id, should_skip=should_skip, should_accept=should_accept)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


__all__ = [
    "ContinuousFuturesAcquisitionReport",
    "build_continuous_futures_acquisition_plan",
    "build_continuous_futures_gap_plan",
    "acquire_continuous_futures_history",
    "repair_continuous_futures_history_gaps",
    "build_continuous_futures_series_from_catalog",
]
