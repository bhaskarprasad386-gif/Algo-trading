"""End-to-end Angel One historical download orchestration.

The service builds bounded provider-safe chunks, downloads sequentially, and
persists each successful chunk immediately into the SQLite catalog. It never
creates synthetic records when the provider returns no data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .angelone_historical import AngelOneHistoricalSource
from .historical_catalog import HistoricalCatalog
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_download_plan import build_one_year_plan
from .historical_ingest import HistoricalIngestionService


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
    """Download real Angel One history into a durable catalog."""

    def __init__(
        self,
        catalog: HistoricalCatalog,
        source: AngelOneHistoricalSource | None = None,
        executor: ResumableHistoricalExecutor | None = None,
    ) -> None:
        self.catalog = catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion)

    def download_one_year(
        self,
        *,
        instrument: str,
        timeframe: str,
        end: datetime,
        source: str = "angelone",
        chunk_days: int = 7,
        retry_attempts: int = 3,
    ) -> AngelOneDownloadReport:
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

        execution = self.executor.run(
            self.source,
            plan,
            retry_attempts=retry_attempts,
            should_skip=complete,
        )
        return AngelOneDownloadReport(
            instrument=instrument,
            timeframe=timeframe,
            start=end.replace(year=end.year - 1),
            end=end,
            execution=execution,
            catalog_count=self.catalog.count(source=source, instrument=instrument),
        )
