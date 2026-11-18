from app.backtesting.cash_future_download_routes import CashFutureDownloadManager
from app.backtesting.historical_download_status import (
    DownloadChunkStatus,
    HistoricalDownloadStatusStore,
)


def _seed_store(tmp_path):
    store = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))
    store.create_job(
        job_id="job-1",
        source="angelone",
        mode="BOTH",
        timeframe="1m",
        spot_instrument="NSE:999:AAA",
        exchange="NFO",
        underlying="AAA",
        start_ns=0,
        end_ns=180,
        requested_chunks=3,
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=0, instrument="NSE:999:AAA",
            start_ns=0, end_ns=60, status="COMPLETE", attempts=1,
            expected_timestamps=2, actual_timestamps=2, missing_timestamps=0,
        )
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=1, instrument="NSE:999:AAA",
            start_ns=60, end_ns=120, status="COMPLETE", attempts=1,
            expected_timestamps=2, actual_timestamps=1, missing_timestamps=1,
            first_missing_ns=120,
        )
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=2, instrument="NFO:101:AAA-FUT",
            start_ns=120, end_ns=180, status="FAILED", attempts=2,
            expected_timestamps=2, actual_timestamps=0, missing_timestamps=2,
            first_missing_ns=120,
        )
    )
    return store


def test_coverage_and_gaps_report_only_missing_data(tmp_path):
    store = _seed_store(tmp_path)
    manager = CashFutureDownloadManager(
        data_db=str(tmp_path / "data.db"),
        contract_db=str(tmp_path / "contracts.db"),
        status_store=store,
    )
    try:
        coverage = manager.coverage("job-1")
        assert coverage["expected_timestamps"] == 6
        assert coverage["actual_timestamps"] == 3
        assert coverage["missing_timestamps"] == 3
        assert coverage["coverage_pct"] == 50.0
        assert coverage["incomplete_chunks"] == 2

        gaps = manager.gaps("job-1")
        assert gaps["count"] == 2
        assert [item["sequence"] for item in gaps["gaps"]] == [1, 2]
        assert gaps["gaps"][0]["instrument"] == "NSE:999:AAA"
        assert gaps["gaps"][1]["instrument"] == "NFO:101:AAA-FUT"
    finally:
        store.close()


def test_incomplete_chunks_preserve_completed_chunks_and_exact_ranges(tmp_path):
    store = _seed_store(tmp_path)
    try:
        incomplete = store.incomplete_chunks("job-1")
        assert [chunk.sequence for chunk in incomplete] == [1, 2]
        assert [(chunk.instrument, chunk.start_ns, chunk.end_ns) for chunk in incomplete] == [
            ("NSE:999:AAA", 60, 120),
            ("NFO:101:AAA-FUT", 120, 180),
        ]
        assert store.chunks("job-1")[0].status == "COMPLETE"
        assert store.chunks("job-1")[0].missing_timestamps == 0
    finally:
        store.close()


def test_repair_delegates_to_resume_only_when_gaps_exist(tmp_path, monkeypatch):
    store = _seed_store(tmp_path)
    manager = CashFutureDownloadManager(
        data_db=str(tmp_path / "data.db"),
        contract_db=str(tmp_path / "contracts.db"),
        status_store=store,
    )
    calls = []
    monkeypatch.setattr(manager, "resume", lambda job_id, retry_attempts=3: calls.append((job_id, retry_attempts)))
    try:
        manager.repair("job-1", retry_attempts=5)
        assert calls == [("job-1", 5)]
    finally:
        store.close()
