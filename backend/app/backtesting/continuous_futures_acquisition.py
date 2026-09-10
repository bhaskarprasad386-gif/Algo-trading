"""Historical acquisition orchestration for continuous futures chains."""

from __future__ import annotations

from dataclasses import dataclass

from .continuous_futures import build_continuous_futures_series_from_catalog
from .fno_rollover import FNORolloverWindow, validate_futures_rollover_chain
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


def _validate_rollover_windows(windows: tuple[FNORolloverWindow, ...]) -> None:
    if not windows:
        return
    first = windows[0]
    validate_futures_rollover_chain(
        windows,
        underlying=first.underlying,
        instrument_type=first.instrument_type,
    )


def _window_sessions(window: FNORolloverWindow, calendar: TradingCalendar) -> tuple[tuple[int, int], ...]:
    return tuple((session.start_ns, session.end_ns) for session in calendar.sessions_between(window.start_date, window.end_date))


def build_continuous_futures_acquisition_plan(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build stable provider requests only inside active-contract trading sessions."""
    _validate_inputs(source=source, timeframe=timeframe, interval_ns=interval_ns, max_request_ns=max_request_ns)
    windows = tuple(windows)
    _validate_rollover_windows(windows)
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
    """Build repair requests for every missing cadence point inside active sessions."""
    _validate_inputs(source=source, timeframe=timeframe, interval_ns=interval_ns, max_request_ns=max_request_ns)
    windows = tuple(windows)
    _validate_rollover_windows(windows)
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


def _plan_from_metadata(metadata: tuple[dict[str, object], ...]) -> HistoricalSyncPlan:
    return HistoricalSyncPlan(tuple(HistoricalFetchRequest(
        source=str(item["source"]), instrument=str(item["instrument"]), timeframe=str(item["timeframe"]),
        start_ns=int(item["start_ns"]), end_ns=int(item["end_ns"]),
    ) for item in metadata))


class _MissingRangeSource:
    """Translate a retry of a Cash-Future chunk into only its currently missing ranges."""
    def __init__(self, catalog: HistoricalCatalog, source: HistoricalSource, interval_ns: int) -> None:
        self.catalog, self.source, self.interval_ns = catalog, source, interval_ns

    def fetch(self, request: HistoricalFetchRequest):
        ranges = _missing_ranges(self.catalog, source=request.source, instrument=request.instrument, timeframe=request.timeframe, session_start_ns=request.start_ns, session_end_ns=request.end_ns, interval_ns=self.interval_ns)
        for start_ns, end_ns in ranges:
            yield from self.source.fetch(HistoricalFetchRequest(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=start_ns, end_ns=end_ns))


def _runner(catalog: HistoricalCatalog, source: HistoricalSource, interval_ns: int, executor: ResumableHistoricalExecutor | None) -> ResumableHistoricalExecutor:
    return executor or ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)


def acquire_continuous_futures_history(
    catalog: HistoricalCatalog, source: HistoricalSource, windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source_name: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None, job_store: HistoricalJobStore | None = None,
    job_id: str | None = None, run_id: str | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Acquire the stable full chain plan with partial-response gap-aware retries."""
    windows = tuple(windows)
    plan = build_continuous_futures_acquisition_plan(windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    runner = _runner(catalog, source, interval_ns, executor)
    gap_aware_source = _MissingRangeSource(catalog, source, interval_ns)
    should_skip = lambda request: _complete(catalog, request, interval_ns)
    should_accept = lambda request, result: _complete(catalog, request, interval_ns)
    if job_store is None:
        if job_id is not None or run_id is not None:
            raise ValueError("job_id and run_id require job_store")
        execution = runner.run(gap_aware_source, plan, should_skip=should_skip, should_accept=should_accept)
    else:
        if not job_id or not run_id:
            raise ValueError("job_store requires both job_id and run_id")
        execution = runner.run_durable(gap_aware_source, plan, job_store=job_store, job_id=job_id, run_id=run_id, should_skip=should_skip, should_accept=should_accept)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


def repair_continuous_futures_history_gaps(
    catalog: HistoricalCatalog, source: HistoricalSource, windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *, source_name: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None, job_store: HistoricalJobStore | None = None,
    job_id: str | None = None, run_id: str | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Run a durable repair plan that remains stable across catalog changes and restarts."""
    windows = tuple(windows)
    if job_store is not None and job_id and run_id:
        try:
            existing = job_store.get(job_id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.run_id != run_id:
                raise ValueError("existing historical job does not match run or plan")
            persisted = job_store.plan_metadata(job_id)
            plan = _plan_from_metadata(persisted) if persisted is not None else build_continuous_futures_gap_plan(catalog, windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
        else:
            plan = build_continuous_futures_gap_plan(catalog, windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    else:
        plan = build_continuous_futures_gap_plan(catalog, windows, source=source_name, timeframe=timeframe, interval_ns=interval_ns, calendar=calendar, max_request_ns=max_request_ns)
    runner = _runner(catalog, source, interval_ns, executor)
    gap_aware_source = _MissingRangeSource(catalog, source, interval_ns)
    should_skip = lambda request: _complete(catalog, request, interval_ns)
    should_accept = lambda request, result: _complete(catalog, request, interval_ns)
    if job_store is None:
        if job_id is not None or run_id is not None:
            raise ValueError("job_id and run_id require job_store")
        execution = runner.run(gap_aware_source, plan, should_skip=should_skip, should_accept=should_accept)
    else:
        if not job_id or not run_id:
            raise ValueError("job_id and run_id require both job_id and run_id")
        execution = runner.run_durable(gap_aware_source, plan, job_store=job_store, job_id=job_id, run_id=run_id, should_skip=should_skip, should_accept=should_accept)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


__all__ = [
    "ContinuousFuturesAcquisitionReport", "build_continuous_futures_acquisition_plan",
    "build_continuous_futures_gap_plan", "acquire_continuous_futures_history",
    "repair_continuous_futures_history_gaps", "build_continuous_futures_series_from_catalog",
]
