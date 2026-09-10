"""Resumable historical download execution with durable per-chunk progress."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .historical_ingest import HistoricalIngestionService, HistoricalSource, HistoricalSyncResult
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan
from .provider_retry import ProviderRetryPolicy


@dataclass(frozen=True)
class DownloadExecutionResult:
    results: tuple[HistoricalSyncResult, ...]
    failed_request_index: int | None = None
    skipped_request_indices: tuple[int, ...] = ()
    completed_count: int | None = None

    @property
    def completed_chunks(self) -> int:
        return self.completed_count if self.completed_count is not None else len(self.results)

    @property
    def skipped_chunks(self) -> int:
        return len(self.skipped_request_indices)

    @property
    def processed_chunks(self) -> int:
        return self.completed_chunks + self.skipped_chunks


class ResumableHistoricalExecutor:
    """Execute bounded requests sequentially; optionally retain no historical results."""

    def __init__(self, service: HistoricalIngestionService, *, sleep: Callable[[float], None] = time.sleep, collect_results: bool = True) -> None:
        self.service = service
        self.sleep = sleep
        self.collect_results = collect_results

    @staticmethod
    def _request_metadata(request: object) -> dict[str, object]:
        return {"source": getattr(request, "source"), "instrument": getattr(request, "instrument"), "timeframe": getattr(request, "timeframe"), "start_ns": getattr(request, "start_ns"), "end_ns": getattr(request, "end_ns")}

    def run(self, source: HistoricalSource, plan: HistoricalSyncPlan, *, retry_attempts: int = 3, retry_delay_seconds: float = 1.0, batch_size: int = 1024, should_skip: Callable[[object], bool] | None = None, should_accept: Callable[[object, HistoricalSyncResult], bool] | None = None, on_batch: Callable[[int, int], None] | None = None, on_chunk_start: Callable[[int, object, int], None] | None = None, on_chunk_skip: Callable[[int, object], None] | None = None, on_chunk_complete: Callable[[int, object, HistoricalSyncResult, int], None] | None = None, on_chunk_failed: Callable[[int, object, Exception, int], None] | None = None, retry_policy: ProviderRetryPolicy | None = None) -> DownloadExecutionResult:
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        results: list[HistoricalSyncResult] = []
        skipped: list[int] = []
        completed_count = 0
        for index, request in enumerate(plan.requests):
            if should_skip is not None and should_skip(request):
                skipped.append(index)
                if on_chunk_skip is not None:
                    on_chunk_skip(index, request)
                continue
            last_error: Exception | None = None
            attempts = 0
            while attempts < retry_attempts:
                attempts += 1
                if on_chunk_start is not None:
                    on_chunk_start(index, request, attempts)
                try:
                    if retry_policy is not None:
                        retry_policy.before_attempt()
                    result = self.service.sync_streaming(source, request, batch_size=batch_size, on_batch=on_batch)
                    if should_accept is not None and not should_accept(request, result):
                        raise ValueError("historical chunk failed completeness validation")
                    completed_count += 1
                    if self.collect_results:
                        results.append(result)
                    last_error = None
                    if on_chunk_complete is not None:
                        on_chunk_complete(index, request, result, attempts)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempts >= retry_attempts or (retry_policy is not None and not retry_policy.is_transient(exc)):
                        break
                    delay = retry_policy.delay(attempts) if retry_policy is not None else retry_delay_seconds * (2 ** (attempts - 1))
                    if delay > 0:
                        (retry_policy.sleeper if retry_policy is not None else self.sleep)(delay)
            if last_error is not None:
                if on_chunk_failed is not None:
                    on_chunk_failed(index, request, last_error, attempts)
                return DownloadExecutionResult(tuple(results), index, tuple(skipped), completed_count=completed_count)
        return DownloadExecutionResult(tuple(results), None, tuple(skipped), completed_count=completed_count)

    def run_durable(self, source: HistoricalSource, plan: HistoricalSyncPlan, *, job_store: HistoricalJobStore, job_id: str, run_id: str, retry_attempts: int = 3, retry_delay_seconds: float = 1.0, batch_size: int = 1024, on_batch: Callable[[int, int], None] | None = None, on_chunk_start: Callable[[int, object, int], None] | None = None, on_chunk_complete: Callable[[int, object, HistoricalSyncResult, int], None] | None = None, on_chunk_failed: Callable[[int, object, Exception, int], None] | None = None, retry_policy: ProviderRetryPolicy | None = None) -> DownloadExecutionResult:
        """Run a plan against a durable job ledger and resume only unfinished chunks."""
        metadata = tuple(self._request_metadata(request) for request in plan.requests)
        fingerprint = job_store.fingerprint(metadata)
        try:
            job = job_store.get(job_id)
            if job.run_id != run_id or job.plan_fingerprint != fingerprint or job.total_chunks != len(plan.requests):
                raise ValueError("existing historical job does not match run or plan")
        except KeyError:
            job_store.create(job_id=job_id, run_id=run_id, plan_fingerprint=fingerprint, total_chunks=len(plan.requests))
        job_store.recover_running_chunks(job_id)
        pending = set(job_store.pending_indices(job_id))

        def index_for(request: object) -> int:
            return next(i for i, candidate in enumerate(plan.requests) if candidate is request)
        def should_skip(request: object) -> bool:
            return index_for(request) not in pending
        def mark_start(index: int, request: object, attempt: int) -> None:
            if attempt == 1:
                job_store.start_chunk(job_id, index)
            if on_chunk_start is not None:
                on_chunk_start(index, request, attempt)
        def mark_complete(index: int, request: object, result: HistoricalSyncResult, attempt: int) -> None:
            job_store.complete_chunk(job_id, index)
            if on_chunk_complete is not None:
                on_chunk_complete(index, request, result, attempt)
        def mark_failed(index: int, request: object, error: Exception, attempts: int) -> None:
            job_store.fail_chunk(job_id, index, str(error), recoverable=True)
            if on_chunk_failed is not None:
                on_chunk_failed(index, request, error, attempts)

        result = self.run(source, plan, retry_attempts=retry_attempts, retry_delay_seconds=retry_delay_seconds, batch_size=batch_size, should_skip=should_skip, on_batch=on_batch, on_chunk_start=mark_start, on_chunk_complete=mark_complete, on_chunk_failed=mark_failed, retry_policy=retry_policy)
        job_store.finish(job_id)
        return result
