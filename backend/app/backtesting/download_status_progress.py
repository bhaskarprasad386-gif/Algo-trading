"""Progress callbacks for durable historical download jobs."""

from __future__ import annotations

from .historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore


def persist_chunk_start(
    store: HistoricalDownloadStatusStore,
    *,
    job_id: str,
    sequence: int,
    instrument: str,
    start_ns: int,
    end_ns: int,
    attempts: int,
) -> None:
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id=job_id,
            sequence=sequence,
            instrument=instrument,
            start_ns=start_ns,
            end_ns=end_ns,
            status="RUNNING",
            attempts=attempts,
        )
    )


def persist_chunk_result(
    store: HistoricalDownloadStatusStore,
    *,
    job_id: str,
    sequence: int,
    instrument: str,
    start_ns: int,
    end_ns: int,
    attempts: int,
    status: str,
    expected_timestamps: int = 0,
    actual_timestamps: int = 0,
    missing_timestamps: int = 0,
    first_missing_ns: int | None = None,
    error: str | None = None,
) -> None:
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id=job_id,
            sequence=sequence,
            instrument=instrument,
            start_ns=start_ns,
            end_ns=end_ns,
            status=status,
            attempts=attempts,
            expected_timestamps=expected_timestamps,
            actual_timestamps=actual_timestamps,
            missing_timestamps=missing_timestamps,
            first_missing_ns=first_missing_ns,
            error=error,
        )
    )
