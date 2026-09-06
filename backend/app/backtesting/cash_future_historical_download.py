"""End-to-end durable Cash-Future historical download orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_download_executor import DownloadExecutionResult, ResumableHistoricalExecutor
from .historical_ingest import HistoricalIngestionService
from .historical_catalog import HistoricalCatalog
from .angelone_historical import AngelOneHistoricalSource


@dataclass(frozen=True)
class CashFutureHistoricalDownloadReport:
    queue: CashFutureDownloadQueue
    spot_execution: DownloadExecutionResult
    future_executions: tuple[DownloadExecutionResult, ...]
    catalog_count: int

    @property
    def completed(self) -> bool:
        return self.spot_execution.failed_request_index is None and all(
            result.failed_request_index is None for result in self.future_executions
        )


class CashFutureHistoricalDownloadService:
    """Download each spot/future segment sequentially and persist every chunk immediately."""

    def __init__(self, catalog: HistoricalCatalog, *, source=None, executor=None) -> None:
        self.catalog = catalog
        self.ingestion = HistoricalIngestionService(catalog)
        self.source = source or AngelOneHistoricalSource()
        self.executor = executor or ResumableHistoricalExecutor(self.ingestion)

    @staticmethod
    def _plan_for_request(request):
        from .historical_sync import HistoricalSyncPlan, build_chunked_plan
        from .historical_download_plan import utc_ns
        from datetime import timedelta
        start = datetime.fromtimestamp(request.start_ns / 1_000_000_000)
        end = datetime.fromtimestamp(request.end_ns / 1_000_000_000)
        chunk = timedelta(days=7)
        return build_chunked_plan(request.source, request.instrument, request.timeframe,
                                  utc_ns(start), utc_ns(end), int(chunk.total_seconds() * 1_000_000_000))

    def run(self, *, spot_instrument: str, exchange: str, underlying: str,
            start: datetime, end: datetime, timeframe: str = "1m", mode: str = "BOTH",
            retry_attempts: int = 3) -> CashFutureHistoricalDownloadReport:
        queue = build_rollover_download_queue(
            catalog=self._contract_catalog,
            spot_instrument=spot_instrument, exchange=exchange, underlying=underlying,
            start=start, end=end, timeframe=timeframe, mode=mode,
        )
        spot_result = self.executor.run(self.source, self._plan_for_request(queue.spot),
                                        retry_attempts=retry_attempts)
        if spot_result.failed_request_index is not None:
            return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(), self.catalog.count())
        future_results = []
        for item in queue.futures:
            result = self.executor.run(self.source, self._plan_for_request(item.request),
                                       retry_attempts=retry_attempts)
            future_results.append(result)
            if result.failed_request_index is not None:
                break
        return CashFutureHistoricalDownloadReport(queue, spot_result, tuple(future_results), self.catalog.count())

    _contract_catalog = None
