"""Bounded Cash-Future historical acquisition orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .cash_future_coverage_manifest import CoverageManifest, manifest_from_catalog, build_coverage_manifest
from .cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from .cash_future_data_coverage import CashFutureDataCoverageAudit, CashFutureDataCoverageReport
from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .cash_future_gap_download import CashFutureGapDownloadPlanner
from .contract_master import ContractMasterCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalIngestionService, HistoricalSource
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan
from .provider_retry import ProviderRetryPolicy, build_provider_retry_policy
from .session_gap_planner import SessionWindow

MARKET_TZ = ZoneInfo("Asia/Kolkata")


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

    @staticmethod
    def _session_days(sessions: tuple[SessionWindow, ...]) -> tuple:
        """Convert session windows into deterministic India-local calendar dates."""
        return tuple(sorted({
            datetime.fromtimestamp(session.start_ns / 1_000_000_000, tz=timezone.utc)
            .astimezone(MARKET_TZ).date()
            for session in sessions
        }))

    def _manifest_repair_instruments(
        self,
        *,
        coverage_store: CashFutureCoverageManifestStore | None,
        source: str,
        timeframe: str,
        requested_instruments: set[str],
    ) -> set[str]:
        """Return only instruments whose persisted manifest still needs repair.

        A complete manifest range is skipped at the provider-request layer. An
        incomplete range remains eligible, after which the catalog gap planner
        narrows work to exact missing cadence chunks. Instruments absent from an
        existing manifest are treated as new acquisition work.
        """
        if coverage_store is None:
            return requested_instruments
        ranges = coverage_store.ranges(source=source, timeframe=timeframe)
        if not ranges:
            return requested_instruments
        known = {item.instrument for item in ranges}
        incomplete = {
            item.instrument
            for item in ranges
            if item.missing_points > 0 or not item.complete
        }
        return (requested_instruments - known) | incomplete

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
        queue: CashFutureDownloadQueue | None = None,
        coverage_store: CashFutureCoverageManifestStore | None = None,
    ) -> tuple[CashFutureDownloadQueue, HistoricalSyncPlan]:
        if queue is None:
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
                session_days=self._session_days(spot_sessions),
            )
        plan = self.planner.plan(
            queue=queue,
            catalog=self.ingestion.catalog,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        repair_instruments = self._manifest_repair_instruments(
            coverage_store=coverage_store,
            source=source,
            timeframe=timeframe,
            requested_instruments={request.instrument for request in queue.all_requests},
        )
        if coverage_store is not None:
            plan = HistoricalSyncPlan(tuple(
                request for request in plan.requests
                if request.instrument in repair_instruments
            ))
        return queue, plan

    def _audit(
        self,
        *,
        queue: CashFutureDownloadQueue,
        mode: str,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]],
    ) -> CashFutureDataCoverageReport:
        return self.coverage.audit(
            queue=queue,
            mode=mode,
            interval_ns=self.interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )

    def _build_manifest(
        self,
        *,
        queue: CashFutureDownloadQueue,
        source: str,
        timeframe: str,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None,
        generated_at: datetime | None = None,
    ) -> CoverageManifest:
        """Build session-scoped coverage so non-trading gaps are never counted."""
        ranges = []
        instruments = {request.instrument for request in queue.all_requests}
        sessions_by_instrument: dict[str, tuple[SessionWindow, ...]] = {
            queue.spot.request.instrument: spot_sessions,
        }
        if future_sessions:
            sessions_by_instrument.update(future_sessions)
        for instrument in sorted(instruments):
            for session in sessions_by_instrument.get(instrument, ()):
                session_manifest = manifest_from_catalog(
                    source=source,
                    instrument=instrument,
                    start_ns=session.start_ns,
                    end_ns=session.end_ns,
                    interval_ns=self.interval_ns,
                    observed_timestamps=self.ingestion.catalog.timestamps(
                        source=source,
                        instrument=instrument,
                        timeframe=timeframe,
                        start_ns=session.start_ns,
                        end_ns=session.end_ns,
                    ),
                    generated_at=generated_at,
                )
                ranges.extend(session_manifest.ranges)
        return build_coverage_manifest(source=source, ranges=ranges, generated_at=generated_at)

    def _persist_manifest(
        self,
        *,
        coverage_store: CashFutureCoverageManifestStore | None,
        queue: CashFutureDownloadQueue,
        source: str,
        timeframe: str,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None,
    ) -> None:
        if coverage_store is None:
            return
        coverage_store.upsert(
            self._build_manifest(
                queue=queue,
                source=source,
                timeframe=timeframe,
                spot_sessions=spot_sessions,
                future_sessions=future_sessions,
            ),
            timeframe=timeframe,
        )

    @staticmethod
    def _durable_job_id(job_store: HistoricalJobStore, base_job_id: str, plan: HistoricalSyncPlan) -> str:
        """Scope durable state to the exact plan so repaired plans get fresh identities."""
        metadata = tuple(
            ResumableHistoricalExecutor._request_metadata(request)
            for request in plan.requests
        )
        fingerprint = job_store.fingerprint(metadata)
        return f"{base_job_id}:plan:{fingerprint}"

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
        job_store: HistoricalJobStore | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        coverage_store: CashFutureCoverageManifestStore | None = None,
        queue: CashFutureDownloadQueue | None = None,
    ) -> CashFutureAcquisitionResult:
        """Download missing chunks and re-plan bounded gaps until coverage stabilizes."""
        durable_args = (job_store is not None, job_id is not None, run_id is not None)
        if any(durable_args) and not all(durable_args):
            raise ValueError("job_store, job_id and run_id must be supplied together")
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
            queue=queue,
            coverage_store=coverage_store,
        )
        progress: list[CashFutureDataCoverageReport] = [
            self._audit(
                queue=queue,
                mode=mode,
                spot_sessions=spot_sessions,
                future_sessions=future_sessions or {},
            )
        ]
        self._persist_manifest(
            coverage_store=coverage_store,
            queue=queue,
            source=source,
            timeframe=timeframe,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        if on_progress is not None:
            on_progress(CashFutureAcquisitionProgress(0, 0, 0, len(plan.requests), progress[-1]))
        total_completed = 0
        total_skipped: list[int] = []
        final_execution = DownloadExecutionResult(())
        effective_retry_policy = retry_policy
        if effective_retry_policy is None and source.strip().lower() == "angelone":
            effective_retry_policy = build_provider_retry_policy(source)

        for pass_index in range(1, max_repair_passes + 1):
            if not plan.requests:
                break
            if job_store is not None:
                durable_plan_job_id = self._durable_job_id(job_store, job_id, plan)
                execution = self.executor.run_durable(
                    self.source,
                    plan,
                    job_store=job_store,
                    job_id=durable_plan_job_id,
                    run_id=run_id,
                    retry_attempts=retry_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                    retry_policy=effective_retry_policy,
                )
            else:
                execution = self.executor.run(
                    self.source,
                    plan,
                    retry_attempts=retry_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                    retry_policy=effective_retry_policy,
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
                    future_sessions=future_sessions or {},
                )
            )
            self._persist_manifest(
                coverage_store=coverage_store,
                queue=queue,
                source=source,
                timeframe=timeframe,
                spot_sessions=spot_sessions,
                future_sessions=future_sessions,
            )
            if execution.failed_request_index is not None:
                if on_progress is not None:
                    on_progress(CashFutureAcquisitionProgress(
                        pass_index, total_completed, len(total_skipped), len(plan.requests), progress[-1]
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
                queue=queue,
                coverage_store=coverage_store,
            )
            pending_chunks = len(next_plan.requests)
            if on_progress is not None:
                on_progress(CashFutureAcquisitionProgress(
                    pass_index, total_completed, len(total_skipped), pending_chunks, progress[-1]
                ))
            if pending_chunks >= len(plan.requests):
                plan = next_plan
                break
            plan = next_plan

        coverage = progress[-1]
        return CashFutureAcquisitionResult(queue, plan, final_execution, coverage, tuple(progress))


__all__ = [
    "CashFutureAcquisitionProgress",
    "CashFutureAcquisitionResult",
    "CashFutureHistoricalAcquisitionService",
]
