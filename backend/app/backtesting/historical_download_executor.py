"""Resumable historical download execution with durable per-chunk progress."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .historical_ingest import HistoricalIngestionService, HistoricalSource, HistoricalSyncResult
from .historical_sync import HistoricalSyncPlan


@dataclass(frozen=True)
class DownloadExecutionResult:
    results: tuple[HistoricalSyncResult, ...]
    failed_request_index: int | None = None

    @property
    def completed_chunks(self) -> int:
        return len(self.results)


class ResumableHistoricalExecutor:
    """Execute bounded requests sequentially; already durable chunks can be skipped."""

    def __init__(self, service: HistoricalIngestionService, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.service = service
        self.sleep = sleep

    def run(
        self,
        source: HistoricalSource,
        plan: HistoricalSyncPlan,
        *,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        should_skip: Callable[[object], bool] | None = None,
    ) -> DownloadExecutionResult:
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        results: list[HistoricalSyncResult] = []
        for index, request in enumerate(plan.requests):
            if should_skip is not None and should_skip(request):
                continue
            last_error: Exception | None = None
            for attempt in range(retry_attempts):
                try:
                    result = self.service.sync(source, request)
                    results.append(result)
                    last_error = None
                    break
                except Exception as exc:  # provider/network errors are retried, then surfaced
                    last_error = exc
                    if attempt + 1 < retry_attempts:
                        self.sleep(retry_delay_seconds * (2 ** attempt))
            if last_error is not None:
                return DownloadExecutionResult(tuple(results), failed_request_index=index)
        return DownloadExecutionResult(tuple(results), failed_request_index=None)
