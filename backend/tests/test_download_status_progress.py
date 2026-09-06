from app.backtesting.download_status_progress import (
    persist_chunk_result,
    persist_chunk_start,
)
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore


def test_chunk_progress_transitions_are_durable():
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id="progress-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=60,
    )
    persist_chunk_start(
        store, job_id="progress-1", sequence=0, instrument="NSE:SPOT",
        start_ns=0, end_ns=60, attempts=1,
    )
    assert store.chunks("progress-1")[0].status == "RUNNING"
    persist_chunk_result(
        store, job_id="progress-1", sequence=0, instrument="NSE:SPOT",
        start_ns=0, end_ns=60, attempts=1, status="COMPLETE",
        expected_timestamps=2, actual_timestamps=2,
    )
    row = store.chunks("progress-1")[0]
    assert row.status == "COMPLETE"
    assert row.expected_timestamps == 2
    assert row.actual_timestamps == 2
    store.close()
