"""Resumable historical download execution with durable per-chunk progress."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .historical_ingest import HistoricalIngestionService, HistoricalSource, HistoricalSyncResult
from .historical_sync import HistoricalSyncPlan


@dataclass(frozen=True)
class DownloadExecutionResult:
    results: tuple[HistoricalSyncResult, ...]
    failed_request_index: int | None = None
    skipped_request_indices: tuple[int, ...] = ()

    @property
    def completed_chunks(self) -> int:
        return len(self.results)

    @property
    def skipped_chunks(self) -> int:
        return len(self.skipped_request_indices)

    @property
    def processed_chunks(self) -> int:
        return self.completed_chunks + self.skipped_chunks


class ResumableHistoricalExecutor:
    """Execute bounded requests sequentially; incomplete chunks are retried."""

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
        should_accept: Callable[[object, HistoricalSyncResult], bool] | None = None,
        on_chunk_start: Callable[[int, object, int], None] | None = None,
        on_chunk_skip: Callable[[int, object], None] | None = None,
        on_chunk_complete: Callable[[int, object, HistoricalSyncResult, int], None] | None = None,
        on_chunk_failed: Callable[[int, object, Exception, int], None] | None = None,
    ) -> DownloadExecutionResult:
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        results: list[HistoricalSyncResult] = []
        skipped: list[int] = []
        for index, request in enumerate(plan.requests):
            if should_skip is not None and should_skip(request):
                skipped.append(index)
                if on_chunk_skip is not None:
                    on_chunk_skip(index, request)
                continue
            last_error: Exception | None = None
            for attempt in range(1, retry_attempts + 1):
                if on_chunk_start is not None:
                    on_chunk_start(index, request, attempt)
                try:
                    result = self.service.sync(source, request)
                    if should_accept is not None and not should_accept(request, result):
                        raise ValueError("historical chunk failed completeness validation")
                    results.append(result)
                    last_error = None
                    if on_chunk_complete is not None:
                        on_chunk_complete(index, request, result, attempt)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < retry_attempts:
                        self.sleep(retry_delay_seconds * (2 ** (attempt - 1)))
            if last_error is not None:
                if on_chunk_failed is not None:
                    on_chunk_failed(index, request, last_error, retry_attempts)
                return DownloadExecutionResult(tuple(results), index, tuple(skipped))
        return DownloadExecutionResult(tuple(results), None, tuple(skipped))
