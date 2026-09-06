"""Progress callbacks for durable historical download jobs."""

from __future__ import annotations

from .historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore


def _refresh_job_counters(store: HistoricalDownloadStatusStore, job_id: str) -> None:
    job = store.job(job_id)
    if job is None:
        return
    chunks = store.chunks(job_id)
    completed = sum(c.status == "COMPLETE" for c in chunks)
    skipped = sum(c.status == "SKIPPED" for c in chunks)
    failed = sum(c.status == "FAILED" for c in chunks)
    store.update_job(job_id, completed_chunks=completed, skipped_chunks=skipped,
                     failed_chunks=failed, catalog_count=job.catalog_count)


def persist_chunk_start(store: HistoricalDownloadStatusStore, *, job_id: str, sequence: int,
                        instrument: str, start_ns: int, end_ns: int, attempts: int) -> None:
    store.upsert_chunk(DownloadChunkStatus(
        job_id=job_id, sequence=sequence, instrument=instrument,
        start_ns=start_ns, end_ns=end_ns, status="RUNNING", attempts=attempts,
    ))
    store.update_job(job_id, status="RUNNING")


def persist_chunk_result(store: HistoricalDownloadStatusStore, *, job_id: str, sequence: int,
                         instrument: str, start_ns: int, end_ns: int, attempts: int,
                         status: str, expected_timestamps: int = 0,
                         actual_timestamps: int = 0, missing_timestamps: int = 0,
                         first_missing_ns: int | None = None, error: str | None = None) -> None:
    store.upsert_chunk(DownloadChunkStatus(
        job_id=job_id, sequence=sequence, instrument=instrument,
        start_ns=start_ns, end_ns=end_ns, status=status, attempts=attempts,
        expected_timestamps=expected_timestamps, actual_timestamps=actual_timestamps,
        missing_timestamps=missing_timestamps, first_missing_ns=first_missing_ns,
        error=error,
    ))
    _refresh_job_counters(store, job_id)
