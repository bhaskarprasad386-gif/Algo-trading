"""Progress callbacks for durable historical download jobs."""

from __future__ import annotations

from .historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore


def _refresh_job_counters(store: HistoricalDownloadStatusStore, job_id: str) -> None:
    job = store.job(job_id)
    if job is None:
        return
    chunks = store.chunks(job_id)
    completed = sum(c.status == "COMPLETE" and c.missing_timestamps == 0 for c in chunks)
    skipped = sum(c.status == "SKIPPED" and c.missing_timestamps == 0 for c in chunks)
    failed = sum(c.status == "FAILED" or c.missing_timestamps > 0 for c in chunks)
    fetched = sum(c.fetched_records for c in chunks)
    inserted = sum(c.inserted_records for c in chunks)
    store.update_job(job_id, completed_chunks=completed, skipped_chunks=skipped,
                     failed_chunks=failed, fetched_records=fetched,
                     inserted_records=inserted, catalog_count=job.catalog_count)


def persist_chunk_start(store: HistoricalDownloadStatusStore, *, job_id: str,
                        sequence: int, instrument: str, start_ns: int, end_ns: int,
                        attempts: int, expected_timestamps: int = 0,
                        actual_timestamps: int = 0, missing_timestamps: int = 0,
                        first_missing_ns: int | None = None) -> None:
    """Persist a RUNNING attempt without erasing prior completeness metrics."""
    existing = next((c for c in store.chunks(job_id)
                     if c.sequence == sequence and c.instrument == instrument), None)
    store.upsert_chunk(DownloadChunkStatus(
        job_id=job_id, sequence=sequence, instrument=instrument,
        start_ns=start_ns, end_ns=end_ns, status="RUNNING", attempts=attempts,
        expected_timestamps=expected_timestamps or (existing.expected_timestamps if existing else 0),
        actual_timestamps=actual_timestamps or (existing.actual_timestamps if existing else 0),
        missing_timestamps=missing_timestamps or (existing.missing_timestamps if existing else 0),
        first_missing_ns=first_missing_ns if first_missing_ns is not None else (existing.first_missing_ns if existing else None),
        fetched_records=existing.fetched_records if existing else 0,
        inserted_records=existing.inserted_records if existing else 0,
    ))
    store.update_job(job_id, status="RUNNING")


def persist_chunk_result(store: HistoricalDownloadStatusStore, *, job_id: str,
                         sequence: int, instrument: str, start_ns: int, end_ns: int,
                         attempts: int, status: str, expected_timestamps: int = 0,
                         actual_timestamps: int = 0, missing_timestamps: int = 0,
                         first_missing_ns: int | None = None, fetched_records: int = 0,
                         inserted_records: int = 0, error: str | None = None,
                         catalog_count: int | None = None) -> None:
    if missing_timestamps > 0 and status in {"COMPLETE", "SKIPPED"}:
        status = "FAILED"
        error = error or "historical chunk is incomplete"
    store.upsert_chunk(DownloadChunkStatus(
        job_id=job_id, sequence=sequence, instrument=instrument,
        start_ns=start_ns, end_ns=end_ns, status=status, attempts=attempts,
        expected_timestamps=expected_timestamps, actual_timestamps=actual_timestamps,
        missing_timestamps=missing_timestamps, first_missing_ns=first_missing_ns,
        fetched_records=fetched_records, inserted_records=inserted_records, error=error,
    ))
    _refresh_job_counters(store, job_id)
    if catalog_count is not None:
        store.update_job(job_id, catalog_count=catalog_count)
