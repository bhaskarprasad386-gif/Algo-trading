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
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureAcquisitionResult:
    """Durable acquisition outcome; raw historical records are never retained here."""

    queue: CashFutureDownloadQueue
    plan: HistoricalSyncPlan
    execution: DownloadExecutionResult
    coverage: CashFutureDataCoverageReport


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
        max_repair_passes: int = 3,
    ) -> CashFutureAcquisitionResult:
        """Download missing chunks and re-plan bounded gaps until coverage stabilizes."""
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
        total_completed = 0
        total_skipped: list[int] = []
        final_execution = DownloadExecutionResult(())

        for _ in range(max_repair_passes):
            if not plan.requests:
                break
            execution = self.executor.run(
                self.source,
                plan,
                retry_attempts=retry_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            total_completed += execution.completed_chunks
            total_skipped.extend(execution.skipped_request_indices)
            final_execution = DownloadExecutionResult(
                (),
                execution.failed_request_index,
                tuple(total_skipped),
                completed_count=total_completed,
            )
            if execution.failed_request_index is not None:
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
            if len(next_plan.requests) >= len(plan.requests):
                plan = next_plan
                break
            plan = next_plan

        coverage = self.coverage.audit(
            queue=queue,
            mode=mode,
            interval_ns=self.interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        return CashFutureAcquisitionResult(queue, plan, final_execution, coverage)


__all__ = ["CashFutureAcquisitionResult", "CashFutureHistoricalAcquisitionService"]
