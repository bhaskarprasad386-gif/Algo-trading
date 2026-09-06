from app.backtesting.historical_download_status import (
    DownloadChunkStatus,
    HistoricalDownloadStatusStore,
)


def test_job_and_chunk_status_round_trip():
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id="job-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=120,
        requested_chunks=2,
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=0, instrument="NSE:SPOT", start_ns=0, end_ns=60,
            status="COMPLETE", attempts=1, expected_timestamps=2,
            actual_timestamps=2,
        )
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=1, instrument="NFO:123:ABC", start_ns=61, end_ns=120,
            status="FAILED", attempts=3, expected_timestamps=2,
            actual_timestamps=1, missing_timestamps=1, first_missing_ns=120,
            error="incomplete chunk",
        )
    )
    store.update_job(
        "job-1", status="FAILED", completed_chunks=1, failed_chunks=1,
        catalog_count=2,
    )

    job = store.job("job-1")
    chunks = store.chunks("job-1")
    assert job is not None
    assert job.status == "FAILED"
    assert job.completed_chunks == 1
    assert job.failed_chunks == 1
    assert len(chunks) == 2
    assert chunks[1].missing_timestamps == 1
    assert chunks[1].first_missing_ns == 120
    store.close()


def test_chunk_upsert_is_idempotent():
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id="job-2", mode="CURRENT", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=60,
    )
    chunk = DownloadChunkStatus(
        job_id="job-2", sequence=0, instrument="NSE:SPOT", start_ns=0, end_ns=60,
        status="RUNNING", attempts=1,
    )
    store.upsert_chunk(chunk)
    store.upsert_chunk(DownloadChunkStatus(**{**chunk.__dict__, "status": "COMPLETE", "attempts": 2}))
    rows = store.chunks("job-2")
    assert len(rows) == 1
    assert rows[0].status == "COMPLETE"
    assert rows[0].attempts == 2
    store.close()
