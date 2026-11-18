"""Durable reconciliation helpers for targeted historical-download repairs."""

from __future__ import annotations

from typing import Callable

from .download_status_progress import persist_chunk_result
from .historical_download_status import HistoricalDownloadStatusStore


def reconcile_download_job(
    store: HistoricalDownloadStatusStore,
    job_id: str,
    metrics_for_chunk: Callable[[object], tuple[int, int, int, int | None]],
) -> tuple[int, int, int]:
    """Recompute every persisted chunk from the durable catalog after repair.

    The original chunk range remains the source of truth; repair sub-ranges are
    never allowed to make the parent chunk look complete prematurely.
    """
    chunks = store.chunks(job_id)
    complete = skipped = incomplete = 0
    for chunk in chunks:
        expected, actual, missing, first_missing = metrics_for_chunk(chunk)
        if missing == 0:
            status = "SKIPPED" if chunk.status == "SKIPPED" else "COMPLETE"
            if status == "SKIPPED":
                skipped += 1
            else:
                complete += 1
        else:
            status = "FAILED" if chunk.status == "FAILED" else "RUNNING"
            incomplete += 1
        persist_chunk_result(
            store,
            job_id=job_id,
            sequence=chunk.sequence,
            instrument=chunk.instrument,
            start_ns=chunk.start_ns,
            end_ns=chunk.end_ns,
            attempts=chunk.attempts,
            status=status,
            expected_timestamps=expected,
            actual_timestamps=actual,
            missing_timestamps=missing,
            first_missing_ns=first_missing,
            fetched_records=chunk.fetched_records,
            inserted_records=chunk.inserted_records,
            error=None if missing == 0 else chunk.error,
        )
    job_status = "COMPLETE" if incomplete == 0 else "FAILED"
    store.update_job(job_id, status=job_status, error=None if incomplete == 0 else "historical download still has missing session timestamps")
    return complete, skipped, incomplete
