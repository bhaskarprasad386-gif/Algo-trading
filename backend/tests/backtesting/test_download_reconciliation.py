from app.backtesting.download_reconciliation import reconcile_download_job
from app.backtesting.historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore


def test_reconciliation_marks_parent_chunk_complete_only_after_catalog_is_complete():
    store = HistoricalDownloadStatusStore(":memory:")
    store.create_job(
        job_id="job-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:1:AAA",
        exchange="NSE", underlying="AAA", start_ns=0, end_ns=120,
        requested_chunks=1,
    )
    store.upsert_chunk(DownloadChunkStatus(
        job_id="job-1", sequence=0, instrument="NSE:1:AAA",
        start_ns=0, end_ns=120, status="RUNNING", attempts=1,
        expected_timestamps=3, actual_timestamps=2, missing_timestamps=1,
        first_missing_ns=60,
    ))

    metrics = lambda chunk: (3, 3, 0, None)
    assert reconcile_download_job(store, "job-1", metrics) == (1, 0, 0)
    chunk = store.chunks("job-1")[0]
    assert chunk.status == "COMPLETE"
    assert chunk.missing_timestamps == 0
    assert store.job("job-1").status == "COMPLETE"


def test_reconciliation_keeps_job_failed_when_missing_data_remains():
    store = HistoricalDownloadStatusStore(":memory:")
    store.create_job(
        job_id="job-2", mode="BOTH", timeframe="1m", spot_instrument="NSE:1:AAA",
        exchange="NSE", underlying="AAA", start_ns=0, end_ns=120,
        requested_chunks=1,
    )
    store.upsert_chunk(DownloadChunkStatus(
        job_id="job-2", sequence=0, instrument="NSE:1:AAA",
        start_ns=0, end_ns=120, status="RUNNING", attempts=1,
    ))

    metrics = lambda chunk: (3, 2, 1, 60)
    assert reconcile_download_job(store, "job-2", metrics) == (0, 0, 1)
    assert store.chunks("job-2")[0].status == "RUNNING"
    assert store.job("job-2").status == "FAILED"
