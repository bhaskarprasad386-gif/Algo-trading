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


def test_incomplete_chunks_preserve_exact_identity_and_exclude_completed():
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id="job-3", mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=300,
    )
    rows = [
        DownloadChunkStatus("job-3", 0, "NSE:SPOT", 0, 99, "COMPLETE", 1),
        DownloadChunkStatus("job-3", 1, "NFO:111:ABC26SEP", 100, 199, "FAILED", 3),
        DownloadChunkStatus("job-3", 2, "NFO:222:ABC26OCT", 200, 299, "RUNNING", 1),
        DownloadChunkStatus("job-3", 3, "NFO:333:ABC26NOV", 300, 300, "SKIPPED", 0),
        DownloadChunkStatus("job-3", 4, "NFO:444:ABC26DEC", 301, 399, "COMPLETE", 1, missing_timestamps=2),
    ]
    for row in rows:
        store.upsert_chunk(row)

    incomplete = store.incomplete_chunks("job-3")
    assert [(row.sequence, row.instrument, row.start_ns, row.end_ns) for row in incomplete] == [
        (1, "NFO:111:ABC26SEP", 100, 199),
        (2, "NFO:222:ABC26OCT", 200, 299),
        (4, "NFO:444:ABC26DEC", 301, 399),
    ]
    store.close()
