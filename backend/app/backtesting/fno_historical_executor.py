"""Durable execution adapter for provider-backed F&O acquisition plans."""

from __future__ import annotations

from dataclasses import dataclass

from .fno_acquisition import FNOAcquisitionPlan, to_fetch_requests
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalSource
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan


@dataclass(frozen=True)
class FNOHistoricalExecutionReport:
    """Auditable execution result for one F&O acquisition run."""

    plan: FNOAcquisitionPlan
    execution: DownloadExecutionResult

    @property
    def completed(self) -> bool:
        return self.execution.failed_request_index is None


def to_historical_sync_plan(plan: FNOAcquisitionPlan, *, source: str) -> HistoricalSyncPlan:
    """Convert the F&O plan into the common bounded sync-plan representation."""
    return HistoricalSyncPlan(requests=to_fetch_requests(plan, source=source))


class FNOHistoricalAcquisitionService:
    """Run F&O history through the durable chunk ledger and ingestion pipeline."""

    def __init__(self, executor: ResumableHistoricalExecutor) -> None:
        self.executor = executor

    def run(
        self,
        source: HistoricalSource,
        plan: FNOAcquisitionPlan,
        *,
        source_name: str,
        job_store: HistoricalJobStore,
        job_id: str,
        run_id: str,
        retry_attempts: int = 3,
        batch_size: int = 1024,
    ) -> FNOHistoricalExecutionReport:
        sync_plan = to_historical_sync_plan(plan, source=source_name)
        execution = self.executor.run_durable(
            source,
            sync_plan,
            job_store=job_store,
            job_id=job_id,
            run_id=run_id,
            retry_attempts=retry_attempts,
            batch_size=batch_size,
        )
        return FNOHistoricalExecutionReport(plan=plan, execution=execution)


__all__ = [
    "FNOHistoricalExecutionReport",
    "FNOHistoricalAcquisitionService",
    "to_historical_sync_plan",
]
