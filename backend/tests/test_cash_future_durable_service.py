from datetime import datetime, timezone

import pytest

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan
from app.backtesting.session_gap_planner import SessionWindow
from tests.test_cash_future_historical_acquisition import FakeHistoricalSource, _contract_catalog


def _service(tmp_path):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    return CashFutureHistoricalAcquisitionService(
        ingestion,
        FakeHistoricalSource(),
        _contract_catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )


def _sessions():
    return (
        SessionWindow(
            int(datetime(2026, 1, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
            int(datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        ),
    )


def test_acquire_routes_to_durable_executor_when_configured(tmp_path, monkeypatch):
    service = _service(tmp_path)
    store = HistoricalJobStore(tmp_path / "jobs.db")
    calls = []

    def run_durable(source, plan, **kwargs):
        calls.append(kwargs)
        from app.backtesting.historical_download_executor import DownloadExecutionResult
        return DownloadExecutionResult(())

    monkeypatch.setattr(service.executor, "run_durable", run_durable)
    monkeypatch.setattr(service.executor, "run", lambda *args, **kwargs: pytest.fail("non-durable executor path used"))

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=_sessions(), future_sessions={"NFO:101:SBINJAN": _sessions()},
        timeframe="1m", mode="CURRENT", source="custom-test-provider",
        retry_attempts=1, max_repair_passes=1,
        job_store=store, job_id="cash-future", run_id="run-1",
    )

    assert result.execution.failed_request_index is None
    assert len(calls) == 1
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["job_id"].startswith("cash-future:plan:")
    assert calls[0]["job_id"] != "cash-future"


def test_acquire_rejects_partial_durable_configuration(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="must be supplied together"):
        service.acquire(
            spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
            start=datetime(2026, 1, 29, tzinfo=timezone.utc),
            end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
            spot_sessions=_sessions(), future_sessions={"NFO:101:SBINJAN": _sessions()},
            timeframe="1m", mode="CURRENT", source="custom-test-provider",
            max_repair_passes=1, job_store=HistoricalJobStore(tmp_path / "jobs.db"),
        )


def test_plan_fingerprint_scopes_repair_pass_job_identity(tmp_path):
    service = _service(tmp_path)
    store = HistoricalJobStore(tmp_path / "jobs.db")
    sessions = _sessions()
    _, plan_a = service.prepare(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=sessions, future_sessions={"NFO:101:SBINJAN": sessions},
        timeframe="1m", mode="CURRENT", source="custom-test-provider",
    )
    _, plan_b = service.prepare(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 4, tzinfo=timezone.utc),
        spot_sessions=(SessionWindow(sessions[0].start_ns, sessions[0].end_ns + 60 * 1_000_000_000),),
        future_sessions={"NFO:101:SBINJAN": (SessionWindow(sessions[0].start_ns, sessions[0].end_ns + 60 * 1_000_000_000),)},
        timeframe="1m", mode="CURRENT", source="custom-test-provider",
    )

    assert isinstance(plan_a, HistoricalSyncPlan)
    assert isinstance(plan_b, HistoricalSyncPlan)
    job_a = service._durable_job_id(store, "cash-future", plan_a)
    job_b = service._durable_job_id(store, "cash-future", plan_b)
    assert job_a != job_b
