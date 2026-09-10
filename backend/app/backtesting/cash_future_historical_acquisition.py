"""Bounded Cash-Future historical acquisition orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .cash_future_data_coverage import CashFutureDataCoverageAudit, CashFutureDataCoverageReport
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .cash_future_gap_download import CashFutureGapDownloadPlanner
from .contract_master import ContractMasterCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalIngestionService, HistoricalSource
from .historical_sync import HistoricalSyncPlan
from .provider_retry import ProviderRetryPolicy
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureAcquisitionProgress:
    """RAM-safe progress event; contains coverage only, never raw historical rows."""

    pass_index: int
    completed_chunks: int
    skipped_chunks: int
    pending_chunks: int
    coverage: CashFutureDataCoverageReport


@dataclass(frozen=True)
class CashFutureAcquisitionResult:
    """Durable acquisition outcome; raw historical records are never retained here."""

    queue: CashFutureDownloadQueue
    plan: HistoricalSyncPlan
    execution: DownloadExecutionResult
    coverage: CashFutureDataCoverageReport
    progress: tuple[CashFutureDataCoverageReport, ...] = ()


class CashFutureHistoricalAcquisitionService:
    """Plan and repair Cash + current/next future history without RAM accumulation."""

    def __init__(
        self,
        ingestion: HistoricalIngestionService,
        source: HistoricalSource,
        contract_master: ContractMasterCatalog,
        *,
        interval_ns: int,
        max_request_ns: int,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.ingestion = ingestion
        self.source = source
        self.contract_master = contract_master
        self.planner = CashFutureGapDownloadPlanner(
            interval_ns=interval_ns,
            max_request_ns=max_request_ns,
        )
        self.interval_ns = interval_ns
        self.executor = ResumableHistoricalExecutor(
            ingestion,
            **({"sleep": sleep} if sleep is not None else {}),
            collect_results=False,
        )
        self.coverage = CashFutureDataCoverageAudit(ingestion.catalog)

    def prepare(
        self,
        *,
        spot_instrument: str,
        exchange: str,
        underlying: str,
        start: datetime,
        end: datetime,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
        timeframe: str = "1m",
        mode: str = "BOTH",
        source: str = "angelone",
    ) -> tuple[CashFutureDownloadQueue, HistoricalSyncPlan]:
        queue = build_rollover_download_queue(
            catalog=self.contract_master,
            spot_instrument=spot_instrument,
            exchange=exchange,
            underlying=underlying,
            start=start,
            end=end,
            timeframe=timeframe,
            mode=mode,
            source=source,
        )
        plan = self.planner.plan(
            queue=queue,
            catalog=self.ingestion.catalog,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        return queue, plan

    def _audit(
        self,
        *,
        queue: CashFutureDownloadQueue,
        mode: str,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None,
    ) -> CashFutureDataCoverageReport:
        return self.coverage.audit(
            queue=queue,
            mode=mode,
            interval_ns=self.interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )

    def acquire(
        self,
        *,
        spot_instrument: str,
        exchange: str,
        underlying: str,
        start: datetime,
        end: datetime,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
        timeframe: str = "1m",
        mode: str = "BOTH",
        source: str = "angelone",
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        retry_policy: ProviderRetryPolicy | None = None,
        max_repair_passes: int = 3,
        on_progress: Callable[[CashFutureAcquisitionProgress], None] | None = None,
    ) -> CashFutureAcquisitionResult:
        """Download missing chunks and re-plan bounded gaps until coverage stabilizes.

        ``progress`` contains a coverage snapshot before acquisition and after every
        completed repair pass. ``on_progress`` emits RAM-safe information synchronously,
        allowing Android/server UIs to render progress during long jobs without retaining
        raw bars or executor results.
        """
        if max_repair_passes < 1:
            raise ValueError("max_repair_passes must be positive")

        queue, plan = self.prepare(
            spot_instrument=spot_instrument,
            exchange=exchange,
            underlying=underlying,
            start=start,
            end=end,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
            timeframe=timeframe,
            mode=mode,
            source=source,
        )
        progress: list[CashFutureDataCoverageReport] = [
            self._audit(
                queue=queue,
                mode=mode,
                spot_sessions=spot_sessions,
                future_sessions=future_sessions,
            )
        ]
        if on_progress is not None:
            on_progress(CashFutureAcquisitionProgress(0, 0, 0, len(plan.requests), progress[-1]))
        total_completed = 0
        total_skipped: list[int] = []
        final_execution = DownloadExecutionResult(())

        for pass_index in range(1, max_repair_passes + 1):
            if not plan.requests:
                break
            execution = self.executor.run(
                self.source,
                plan,
                retry_attempts=retry_attempts,
                retry_delay_seconds=retry_delay_seconds,
                retry_policy=retry_policy,
            )
            total_completed += execution.completed_chunks
            total_skipped.extend(execution.skipped_request_indices)
            final_execution = DownloadExecutionResult(
                (),
                execution.failed_request_index,
                tuple(total_skipped),
                completed_count=total_completed,
            )
            progress.append(
                self._audit(
                    queue=queue,
                    mode=mode,
                    spot_sessions=spot_sessions,
                    future_sessions=future_sessions,
                )
            )
            if execution.failed_request_index is not None:
                if on_progress is not None:
                    on_progress(CashFutureAcquisitionProgress(
                        pass_index,
                        total_completed,
                        len(total_skipped),
                        len(plan.requests),
                        progress[-1],
                    ))
                break
            _, next_plan = self.prepare(
                spot_instrument=spot_instrument,
                exchange=exchange,
                underlying=underlying,
                start=start,
                end=end,
                spot_sessions=spot_sessions,
                future_sessions=future_sessions,
                timeframe=timeframe,
                mode=mode,
                source=source,
            )
            pending_chunks = len(next_plan.requests)
            if on_progress is not None:
                on_progress(CashFutureAcquisitionProgress(
                    pass_index,
                    total_completed,
                    len(total_skipped),
                    pending_chunks,
                    progress[-1],
                ))
            if pending_chunks >= len(plan.requests):
                plan = next_plan
                break
            plan = next_plan

        coverage = progress[-1]
        return CashFutureAcquisitionResult(
            queue, plan, final_execution, coverage, tuple(progress)
        )


__all__ = [
    "CashFutureAcquisitionProgress",
    "CashFutureAcquisitionResult",
    "CashFutureHistoricalAcquisitionService",
]
