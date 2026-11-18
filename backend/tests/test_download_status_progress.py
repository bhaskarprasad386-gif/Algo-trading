from app.backtesting.download_status_progress import persist_chunk_result, persist_chunk_start
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore


def _store(job_id: str = "progress-1") -> HistoricalDownloadStatusStore:
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id=job_id, mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=60,
    )
    return store


def test_chunk_progress_transitions_are_durable():
    store = _store()
    persist_chunk_start(store, job_id="progress-1", sequence=0, instrument="NSE:SPOT",
                        start_ns=0, end_ns=60, attempts=1)
    assert store.chunks("progress-1")[0].status == "RUNNING"
    persist_chunk_result(store, job_id="progress-1", sequence=0, instrument="NSE:SPOT",
                         start_ns=0, end_ns=60, attempts=1, status="COMPLETE",
                         expected_timestamps=2, actual_timestamps=2,
                         fetched_records=2, inserted_records=2)
    row = store.chunks("progress-1")[0]
    assert row.status == "COMPLETE"
    assert row.fetched_records == 2
    assert row.inserted_records == 2
    job = store.job("progress-1")
    assert job is not None
    assert job.fetched_records == 2
    assert job.inserted_records == 2
    store.close()


def test_retry_does_not_double_count_previous_chunk_result():
    store = _store("retry-1")
    persist_chunk_result(store, job_id="retry-1", sequence=0, instrument="NSE:SPOT",
                         start_ns=0, end_ns=60, attempts=1, status="COMPLETE",
                         fetched_records=10, inserted_records=10)
    persist_chunk_start(store, job_id="retry-1", sequence=0, instrument="NSE:SPOT",
                        start_ns=0, end_ns=60, attempts=2)
    assert store.job("retry-1").fetched_records == 10
    persist_chunk_result(store, job_id="retry-1", sequence=0, instrument="NSE:SPOT",
                         start_ns=0, end_ns=60, attempts=2, status="COMPLETE",
                         fetched_records=12, inserted_records=2)
    job = store.job("retry-1")
    assert job is not None
    assert job.fetched_records == 12
    assert job.inserted_records == 2
    store.close()


def test_job_counters_sum_independent_spot_and_future_chunks():
    store = _store("mixed-1")
    for sequence, instrument, fetched, inserted in (
        (0, "NSE:SPOT", 100, 100),
        (1, "NFO:1:FUT", 100, 80),
    ):
        persist_chunk_result(store, job_id="mixed-1", sequence=sequence, instrument=instrument,
                             start_ns=0, end_ns=60, attempts=1, status="COMPLETE",
                             fetched_records=fetched, inserted_records=inserted)
    job = store.job("mixed-1")
    assert job is not None
    assert job.fetched_records == 200
    assert job.inserted_records == 180
    assert job.completed_chunks == 2
    store.close()


def test_incomplete_chunk_is_replayed_instead_of_marked_complete():
    store = _store("incomplete-1")
    persist_chunk_result(
        store, job_id="incomplete-1", sequence=0, instrument="NSE:SPOT",
        start_ns=0, end_ns=60, attempts=1, status="COMPLETE",
        expected_timestamps=3, actual_timestamps=2, missing_timestamps=1,
        first_missing_ns=60, fetched_records=2, inserted_records=2,
    )
    row = store.chunks("incomplete-1")[0]
    assert row.status == "FAILED"
    assert row.missing_timestamps == 1
    assert store.incomplete_chunks("incomplete-1")[0].sequence == 0
    job = store.job("incomplete-1")
    assert job is not None
    assert job.completed_chunks == 0
    assert job.failed_chunks == 1
    store.close()


def test_legacy_status_schema_migrates_new_counters():
    import sqlite3
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as handle:
        db = sqlite3.connect(handle.name)
        db.execute("""CREATE TABLE download_jobs (
            job_id TEXT PRIMARY KEY, mode TEXT NOT NULL, timeframe TEXT NOT NULL,
            spot_instrument TEXT NOT NULL, exchange TEXT NOT NULL, underlying TEXT NOT NULL,
            start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL,
            requested_chunks INTEGER NOT NULL DEFAULT 0, completed_chunks INTEGER NOT NULL DEFAULT 0,
            skipped_chunks INTEGER NOT NULL DEFAULT 0, failed_chunks INTEGER NOT NULL DEFAULT 0,
            catalog_count INTEGER NOT NULL DEFAULT 0, updated_at_ns INTEGER NOT NULL, error TEXT
        )""")
        db.execute("""CREATE TABLE download_chunks (
            job_id TEXT NOT NULL, sequence INTEGER NOT NULL, instrument TEXT NOT NULL,
            start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, expected_timestamps INTEGER NOT NULL DEFAULT 0,
            actual_timestamps INTEGER NOT NULL DEFAULT 0, missing_timestamps INTEGER NOT NULL DEFAULT 0,
            first_missing_ns INTEGER, error TEXT, updated_at_ns INTEGER NOT NULL,
            PRIMARY KEY(job_id, sequence), FOREIGN KEY(job_id) REFERENCES download_jobs(job_id)
        )""")
        db.commit()
        db.close()
        store = HistoricalDownloadStatusStore(handle.name)
        store.create_job(job_id="legacy-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
                          exchange="NFO", underlying="ABC", start_ns=0, end_ns=60)
        job = store.job("legacy-1")
        assert job is not None
        assert job.fetched_records == 0
        assert job.inserted_records == 0
        store.close()
