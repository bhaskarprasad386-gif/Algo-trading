"""End-to-end Angel One historical download orchestration.

The service builds bounded provider-safe chunks, downloads sequentially, and
persists each successful chunk immediately into the SQLite catalog. It never
creates synthetic records when the provider returns no data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .angelone_historical import AngelOneHistoricalSource
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_download_plan import build_one_year_plan, one_year_range
from .historical_ingest import HistoricalIngestionService
from .historical_job_store import HistoricalJobStore
from .provider_retry import ProviderRetryPolicy, build_provider_retry_policy


@dataclass(frozen=True)
class AngelOneDownloadReport:
    instrument: str
    timeframe: str
    start: datetime
    end: datetime
    execution: DownloadExecutionResult
    catalog_count: int

    @property
    def completed(self) -> bool:
        return self.execution.failed_request_index is None


class AngelOneHistoricalDownloadService:
    """Download real Angel One history into a durable catalog.

    Durable job identifiers are optional, but when supplied they make the
    one-year acquisition restart-safe and prevent a worker crash from forcing
    already completed chunks to be downloaded again.
    """

    def __init__(
        self,
        catalog: HistoricalCatalog,
        source: AngelOneHistoricalSource | None = None,
        executor: ResumableHistoricalExecutor | None = None,
    ) -> None:
        self.catalog = catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion, collect_results=False)

    def download_one_year(
        self,
        *,
        instrument: str,
        timeframe: str,
        end: datetime,
        source: str = "angelone",
        chunk_days: int = 7,
        retry_attempts: int = 3,
        retry_policy: ProviderRetryPolicy | None = None,
        job_store: HistoricalJobStore | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
    ) -> AngelOneDownloadReport:
        durable_args = (job_store, job_id, run_id)
        if any(value is not None for value in durable_args) and not all(value is not None for value in durable_args):
            raise ValueError("job_store, job_id and run_id must be supplied together")

        plan = build_one_year_plan(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            end=end,
            chunk_days=chunk_days,
        )

        def complete(request) -> bool:
            watermark = self.catalog.watermark(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
            )
            return watermark is not None and watermark >= request.end_ns

        effective_retry_policy = retry_policy
        if effective_retry_policy is None and source.strip().lower() == "angelone":
            effective_retry_policy = build_provider_retry_policy(source)

        if job_store is not None:
            execution = self.executor.run_durable(
                self.source,
                plan,
                job_store=job_store,
                job_id=job_id,
                run_id=run_id,
                retry_attempts=retry_attempts,
                retry_policy=effective_retry_policy,
            )
        else:
            execution = self.executor.run(
                self.source,
                plan,
                retry_attempts=retry_attempts,
                should_skip=complete,
                retry_policy=effective_retry_policy,
            )

        start_ns, _ = one_year_range(end=end)
        start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
        return AngelOneDownloadReport(
            instrument=instrument,
            timeframe=timeframe,
            start=start,
            end=end,
            execution=execution,
            catalog_count=self.catalog.count(source=source, instrument=instrument),
        )
