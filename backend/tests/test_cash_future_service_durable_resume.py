from datetime import datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalIngestionService, HistoricalRecord
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow


class CrashSource:
    def __init__(self):
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated worker crash")
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"close": 101.0})


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(datetime(2026, 1, 1).date(), [
        ContractRecord("NFO", "SBINJAN", "101", datetime(2026, 1, 29).date(), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def _service(history, source):
    return CashFutureHistoricalAcquisitionService(
        HistoricalIngestionService(history), source, _catalog(),
        interval_ns=60 * 1_000_000_000, max_request_ns=60 * 1_000_000_000,
        sleep=lambda _: None,
    )


def test_service_resumes_same_plan_after_worker_crash(tmp_path):
    history = HistoricalCatalog(tmp_path / "history.db")
    jobs = HistoricalJobStore(str(tmp_path / "jobs.db"))
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = datetime(2026, 1, 29, 0, 1, tzinfo=timezone.utc)
    session = SessionWindow(int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000))
    kwargs = dict(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=start, end=end, spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)}, timeframe="1m",
        mode="CURRENT", source="angelone", retry_attempts=1, max_repair_passes=1,
        job_id="cash-future-run", run_id="run-1",
    )
    try:
        _service(history, CrashSource()).acquire(**kwargs, job_store=jobs)
    except RuntimeError as exc:
        assert str(exc) == "simulated worker crash"
    else:
        raise AssertionError("expected worker crash")

    rows = jobs._db.execute("SELECT job_id, plan_fingerprint FROM historical_jobs").fetchall()
    assert len(rows) == 1
    plan_job_id, fingerprint = rows[0]
    assert jobs.chunk_state(plan_job_id, 0)[0] == "running"
    jobs.close()

    resumed_jobs = HistoricalJobStore(str(tmp_path / "jobs.db"))
    resumed = _service(history, CrashSource()).acquire(**kwargs, job_store=resumed_jobs)
    assert resumed.execution.failed_request_index is None
    assert resumed.plan.requests == ()
    assert resumed.coverage.complete
    assert resumed_jobs.get(plan_job_id).plan_fingerprint == fingerprint
    assert resumed_jobs.pending_indices(plan_job_id) == ()
    assert resumed_jobs.get(plan_job_id).state == "completed"
