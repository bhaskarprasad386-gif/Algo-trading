from datetime import datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService, HistoricalRecord
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow


class ResumeSource:
    def __init__(self):
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
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

    # Persist a running chunk exactly as a worker would before a process crash.
    service = _service(history, ResumeSource())
    queue, plan = service.prepare(
        spot_instrument=kwargs["spot_instrument"], exchange=kwargs["exchange"],
        underlying=kwargs["underlying"], start=start, end=end,
        spot_sessions=(session,), future_sessions=kwargs["future_sessions"],
        timeframe="1m", mode="CURRENT", source="angelone",
    )
    durable_id = service._durable_job_id(jobs, kwargs["job_id"], plan)
    metadata = tuple(ResumableHistoricalExecutor._request_metadata(request) for request in plan.requests)
    jobs.create(
        job_id=durable_id,
        run_id=kwargs["run_id"],
        plan_fingerprint=jobs.fingerprint(metadata),
        total_chunks=len(plan.requests),
    )
    jobs.start_chunk(durable_id, 0)
    assert jobs.chunk_state(durable_id, 0)[0] == "running"
    jobs.close()

    resumed_jobs = HistoricalJobStore(str(tmp_path / "jobs.db"))
    resumed_source = ResumeSource()
    resumed = _service(history, resumed_source).acquire(**kwargs, job_store=resumed_jobs)

    assert resumed.execution.failed_request_index is None
    assert resumed.plan.requests == ()
    assert resumed.coverage.complete
    assert resumed_jobs.pending_indices(durable_id) == ()
    assert resumed_jobs.get(durable_id).state == "completed"
    assert resumed_source.calls == len(plan.requests)
