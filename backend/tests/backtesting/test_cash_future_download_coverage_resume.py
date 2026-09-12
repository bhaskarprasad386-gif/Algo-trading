from datetime import datetime, timezone

from app.backtesting.cash_future_download_routes import CashFutureDownloadManager
from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_status import (
    DownloadChunkStatus,
    HistoricalDownloadStatusStore,
)


def _seed_job(store: HistoricalDownloadStatusStore) -> None:
    store.create_job(
        job_id="job-1",
        source="angelonehistoricalsource",
        mode="BOTH",
        timeframe="1m",
        spot_instrument="NSE:1:AAA",
        exchange="NFO",
        underlying="AAA",
        start_ns=0,
        end_ns=180,
        requested_chunks=3,
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=0, instrument="NSE:1:AAA",
            start_ns=0, end_ns=60, status="COMPLETE", attempts=1,
            expected_timestamps=2, actual_timestamps=2, missing_timestamps=0,
            fetched_records=2, inserted_records=2,
        )
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=1, instrument="NSE:1:AAA",
            start_ns=60, end_ns=120, status="FAILED", attempts=1,
            expected_timestamps=2, actual_timestamps=1, missing_timestamps=1,
            first_missing_ns=120,
        )
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="job-1", sequence=2, instrument="NFO:2:AAA-FUT",
            start_ns=120, end_ns=180, status="COMPLETE", attempts=1,
            expected_timestamps=2, actual_timestamps=2, missing_timestamps=0,
            fetched_records=2, inserted_records=2,
        )
    )


def test_coverage_and_gap_views_report_only_the_real_incomplete_chunk(tmp_path):
    store = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))
    catalog = HistoricalCatalog(str(tmp_path / "data.db"))
    manager = CashFutureDownloadManager(
        data_db=str(tmp_path / "data.db"),
        contract_db=str(tmp_path / "contracts.db"),
        status_store=store,
    )
    try:
        _seed_job(store)
        coverage = manager.coverage("job-1")
        assert coverage["expected_timestamps"] == 6
        assert coverage["actual_timestamps"] == 5
        assert coverage["missing_timestamps"] == 1
        assert coverage["coverage_pct"] == 83.33
        assert coverage["incomplete_chunks"] == 1

        gaps = manager.gaps("job-1")
        assert gaps["count"] == 1
        assert gaps["gaps"][0]["sequence"] == 1
        assert gaps["gaps"][0]["first_missing_ns"] == 120
    finally:
        manager.close()
        catalog.close()
        store.close()


def test_resume_plan_reuses_only_incomplete_chunk_identity_and_range(tmp_path):
    store = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))
    catalog = HistoricalCatalog(str(tmp_path / "data.db"))
    try:
        _seed_job(store)
        service = CashFutureHistoricalDownloadService(
            catalog,
            object(),
            source=type("Source", (), {"source_name": "angelonehistoricalsource"})(),
            status_store=store,
        )
        plan, sequences = service._resume_plan("job-1")
        assert sequences == (1,)
        assert len(plan.requests) == 1
        request = plan.requests[0]
        assert request.instrument == "NSE:1:AAA"
        assert request.start_ns == 60
        assert request.end_ns == 120
    finally:
        catalog.close()
        store.close()


def test_duplicate_reingest_remains_deduplicated_for_repair(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "data.db"))
    try:
        timestamp_ns = int(datetime(2026, 1, 5, 5, 30, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        record = {"close": 100.0}
        assert catalog.ingest(
            source="angelone",
            instrument="NSE:1:AAA",
            timeframe="1m",
            timestamp_ns=timestamp_ns,
            payload=record,
        )
        assert not catalog.ingest(
            source="angelone",
            instrument="NSE:1:AAA",
            timeframe="1m",
            timestamp_ns=timestamp_ns,
            payload=record,
        )
        assert catalog.count(source="angelone", instrument="NSE:1:AAA", timeframe="1m") == 1
    finally:
        catalog.close()
