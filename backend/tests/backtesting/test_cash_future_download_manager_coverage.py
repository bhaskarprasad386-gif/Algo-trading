from datetime import datetime, timezone

from app.backtesting.cash_future_download_routes import (
    CashFutureDownloadManager,
    CashFutureDownloadStartRequest,
)
from app.backtesting.historical_download_status import (
    DownloadChunkStatus,
    HistoricalDownloadStatusStore,
)


def _request() -> CashFutureDownloadStartRequest:
    return CashFutureDownloadStartRequest(
        spot_instrument="NSE:1:AAA",
        exchange="NFO",
        underlying="AAA",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        timeframe="1m",
        mode="BOTH",
    )


def _manager(tmp_path):
    status = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))
    manager = CashFutureDownloadManager(
        data_db=str(tmp_path / "catalog.db"),
        contract_db=str(tmp_path / "contracts.db"),
        status_store=status,
    )
    return manager, status


def _seed_job(status, job_id, *, missing):
    status.create_job(
        job_id=job_id,
        source="angelone",
        mode="BOTH",
        timeframe="1m",
        spot_instrument="NSE:1:AAA",
        exchange="NFO",
        underlying="AAA",
        start_ns=0,
        end_ns=120_000_000_000,
        requested_chunks=1,
    )
    status.upsert_chunk(
        DownloadChunkStatus(
            job_id=job_id,
            sequence=0,
            instrument="NSE:1:AAA",
            start_ns=0,
            end_ns=120_000_000_000,
            status="RUNNING" if missing else "COMPLETE",
            attempts=1,
            expected_timestamps=3,
            actual_timestamps=3 - missing,
            missing_timestamps=missing,
        )
    )


def test_manager_does_not_mark_incomplete_repair_complete(tmp_path, monkeypatch):
    manager, status = _manager(tmp_path)
    _seed_job(status, "incomplete", missing=1)

    def leave_incomplete(self, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backtesting.cash_future_download_routes.CashFutureHistoricalDownloadService.run",
        leave_incomplete,
    )

    manager._download_sync("incomplete", _request(), _request().start, _request().end, resume=True)

    job = status.job("incomplete")
    coverage = manager.coverage("incomplete")
    assert job.status == "RUNNING"
    assert coverage["expected_timestamps"] == 3
    assert coverage["actual_timestamps"] == 2
    assert coverage["missing_timestamps"] == 1
    assert coverage["coverage_pct"] == 66.67
    assert coverage["incomplete_chunks"] == 1


def test_manager_marks_complete_only_when_no_incomplete_chunks(tmp_path, monkeypatch):
    manager, status = _manager(tmp_path)
    _seed_job(status, "complete", missing=0)
    status.update_job("complete", status="RUNNING")

    def leave_complete(self, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backtesting.cash_future_download_routes.CashFutureHistoricalDownloadService.run",
        leave_complete,
    )

    manager._download_sync("complete", _request(), _request().start, _request().end, resume=True)

    job = status.job("complete")
    coverage = manager.coverage("complete")
    assert job.status == "COMPLETE"
    assert coverage["expected_timestamps"] == 3
    assert coverage["actual_timestamps"] == 3
    assert coverage["missing_timestamps"] == 0
    assert coverage["coverage_pct"] == 100.0
    assert coverage["incomplete_chunks"] == 0
