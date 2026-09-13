"""Historical Cash-Future download orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_gap_download import CashFutureGapDownloadPlanner
from .cash_future_gap_repair import CashFutureGapRepairPlanner
from .cash_future_download_queue import CashFutureDownloadQueue
from .historical_ingest import HistoricalFetchRequest, HistoricalSyncPlan
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureHistoricalDownloadResult:
    """Result metadata for a historical Cash-Future download."""

    plan: HistoricalSyncPlan
    downloaded: int


class CashFutureHistoricalDownloader:
    """Coordinate historical Cash-Future downloads and durable progress."""

    def __init__(self, *, source_name: str, status_store=None) -> None:
        self.source_name = source_name
        self.status_store = status_store

    def _chunk_callbacks(self, *, store, job_id, sequence_offset=0, sequence_numbers=None):
        def status_sequence(index: int) -> int:
            return sequence_numbers[index] if sequence_numbers is not None else sequence_offset + index

        def status_request(index, request):
            if sequence_numbers is None:
                return request
            sequence = status_sequence(index)
            parent = next((chunk for chunk in store.chunks(job_id) if chunk.sequence == sequence), None)
            if parent is None:
                raise KeyError(f"download chunk sequence not found: {sequence}")
            return HistoricalFetchRequest(
                self.source_name, parent.instrument, request.timeframe, parent.start_ns, parent.end_ns
            )

        def start(index, request, attempt):
            from .download_status_progress import persist_chunk_start
            target = status_request(index, request)
            persist_chunk_start(
                store,
                job_id=job_id,
                sequence=status_sequence(index),
                instrument=target.instrument,
                start_ns=target.start_ns,
                end_ns=target.end_ns,
                attempts=attempt,
            )

        def skip(index, request):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            persist_chunk_result(
                store,
                job_id=job_id,
                sequence=status_sequence(index),
                instrument=target.instrument,
                start_ns=target.start_ns,
                end_ns=target.end_ns,
                attempts=0,
                status="SKIPPED",
                fetched_records=0,
                inserted_records=0,
                expected_timestamps=expected,
                actual_timestamps=actual,
                missing_timestamps=missing,
                first_missing_ns=first_missing,
            )

        def complete(index, request, result, attempt):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            existing = next(
                (chunk for chunk in store.chunks(job_id) if chunk.sequence == status_sequence(index)),
                None,
            )
            fetched = (existing.fetched_records if existing else 0) + result.fetched
            inserted = (existing.inserted_records if existing else 0) + result.inserted
            persist_chunk_result(
                store,
                job_id=job_id,
                sequence=status_sequence(index),
                instrument=target.instrument,
                start_ns=target.start_ns,
                end_ns=target.end_ns,
                attempts=attempt,
                status="COMPLETED",
                fetched_records=fetched,
                inserted_records=inserted,
                expected_timestamps=expected,
                actual_timestamps=actual,
                missing_timestamps=missing,
                first_missing_ns=first_missing,
            )

        def failed(index, request, error, attempts):
            from .download_status_progress import persist_chunk_result
            target = status_request(index, request)
            expected, actual, missing, first_missing = self._chunk_metrics(target)
            persist_chunk_result(
                store,
                job_id=job_id,
                sequence=status_sequence(index),
                instrument=target.instrument,
                start_ns=target.start_ns,
                end_ns=target.end_ns,
                attempts=attempts,
                status="FAILED",
                error=str(error),
                fetched_records=0,
                inserted_records=0,
                expected_timestamps=expected,
                actual_timestamps=actual,
                missing_timestamps=missing,
                first_missing_ns=first_missing,
            )
            store.update_job(job_id, status="FAILED", error=str(error))

        return {
            "on_chunk_start": start,
            "on_chunk_skip": skip,
            "on_chunk_complete": complete,
            "on_chunk_failed": failed,
        }

    def _resume_plan(self, job_id: str) -> tuple[HistoricalSyncPlan, tuple[int, ...]]:
        if self.status_store is None:
            raise ValueError("resume requires a durable status store")
        job = self.status_store.job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.source != self.source_name:
            raise ValueError(
                f"historical download provider mismatch: job={job.source!r}, source={self.source_name!r}"
            )
        chunks = self.status_store.incomplete_chunks(job_id)
        requests = tuple(
            HistoricalFetchRequest(
                self.source_name,
                chunk.instrument,
                job.timeframe,
                chunk.start_ns,
                chunk.end_ns,
            )
            for chunk in chunks
        )
        return HistoricalSyncPlan(requests), tuple(chunk.sequence for chunk in chunks)
