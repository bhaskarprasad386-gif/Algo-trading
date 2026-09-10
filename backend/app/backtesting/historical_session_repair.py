"""Session-aware historical gap repair execution."""

from __future__ import annotations

from dataclasses import dataclass

from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_sync import HistoricalSyncPlan, build_session_gap_plan
from .historical_ingest import HistoricalSource


@dataclass(frozen=True)
class HistoricalSessionRepairReport:
    """Result of repairing only cadence gaps inside trading sessions."""

    plan: HistoricalSyncPlan
    execution: DownloadExecutionResult

    @property
    def completed(self) -> bool:
        return self.execution.failed_request_index is None


class HistoricalSessionRepairService:
    """Build and execute calendar-bounded historical repairs."""

    def __init__(self, executor: ResumableHistoricalExecutor) -> None:
        self.executor = executor

    def repair(
        self,
        source: HistoricalSource,
        *,
        catalog,
        calendar,
        source_name: str,
        instrument: str,
        timeframe: str,
        interval_ns: int,
        start_date,
        end_date,
        max_request_ns: int,
        retry_attempts: int = 3,
    ) -> HistoricalSessionRepairReport:
        plan = build_session_gap_plan(
            catalog,
            calendar,
            source=source_name,
            instrument=instrument,
            timeframe=timeframe,
            interval_ns=interval_ns,
            start_date=start_date,
            end_date=end_date,
            max_request_ns=max_request_ns,
        )
        execution = self.executor.run(
            source,
            plan,
            retry_attempts=retry_attempts,
        )
        return HistoricalSessionRepairReport(plan=plan, execution=execution)
