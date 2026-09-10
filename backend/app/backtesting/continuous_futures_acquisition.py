"""Historical acquisition orchestration for continuous futures chains.

The planner is chain-aware: each request is bounded by the active contract's
rollover window and trading sessions, so it never asks the provider for data
outside the contract's valid lifetime. Existing complete cadence chunks are
skipped; partial chunks are safely re-fetched and deduplicated by the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from .continuous_futures import build_continuous_futures_series_from_catalog
from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalSource
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


def _window_sessions(
    window: FNORolloverWindow,
    calendar: TradingCalendar,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (session.start_ns, session.end_ns)
        for session in calendar.sessions_between(window.start_date, window.end_date)
    )


def build_continuous_futures_acquisition_plan(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *,
    source: str,
    timeframe: str,
    interval_ns: int,
    calendar: TradingCalendar,
    max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build provider requests only inside active-contract trading sessions."""
    if not source.strip():
        raise ValueError("source is required")
    if not timeframe.strip():
        raise ValueError("timeframe is required")
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")

    requests: list[HistoricalFetchRequest] = []
    for window in windows:
        instrument = f"NFO:{window.contract_token}"
        for session_start, session_end in _window_sessions(window, calendar):
            requests.extend(
                build_chunked_plan(
                    source=source,
                    instrument=instrument,
                    timeframe=timeframe,
                    start_ns=session_start,
                    end_ns=session_end,
                    chunk_ns=max_request_ns,
                ).requests
            )
    return HistoricalSyncPlan(tuple(requests))


def acquire_continuous_futures_history(
    catalog: HistoricalCatalog,
    source: HistoricalSource,
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *,
    source_name: str,
    timeframe: str,
    interval_ns: int,
    calendar: TradingCalendar,
    max_request_ns: int,
    executor: ResumableHistoricalExecutor | None = None,
) -> ContinuousFuturesAcquisitionReport:
    """Fill only missing chain/session cadence chunks and persist them immediately."""
    windows = tuple(windows)
    plan = build_continuous_futures_acquisition_plan(
        windows,
        source=source_name,
        timeframe=timeframe,
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
    )
    runner = executor or ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
    )

    def complete(request: HistoricalFetchRequest) -> bool:
        """Check a whole chunk with one catalog range query.

        The previous implementation issued one SQLite query per expected
        timestamp. Large historical plans can contain millions of expected
        points, so batch the lookup into a single range query and compare the
        returned timestamp set locally. This preserves exact completeness
        semantics while avoiding an N-query-per-chunk database bottleneck.
        """
        expected = range(request.start_ns, request.end_ns + 1, interval_ns)
        if not expected:
            return True
        present = set(
            catalog.timestamps(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                start_ns=request.start_ns,
                end_ns=request.end_ns,
            )
        )
        return all(timestamp in present for timestamp in expected)

    execution = runner.run(source, plan, should_skip=complete)
    return ContinuousFuturesAcquisitionReport(windows, plan, execution)


def _day_start_ns(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _day_end_ns(value: date) -> int:
    return int(datetime.combine(value, time.max, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


__all__ = [
    "ContinuousFuturesAcquisitionReport",
    "build_continuous_futures_acquisition_plan",
    "acquire_continuous_futures_history",
    "build_continuous_futures_series_from_catalog",
]
