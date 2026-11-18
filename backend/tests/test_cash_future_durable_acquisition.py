from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow


class DurableFakeSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        timestamp = request.start_ns
        while timestamp <= request.end_ns:
            yield HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"close": 100.0},
            )
            timestamp += 60 * 1_000_000_000


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def _session():
    start = int(datetime(2026, 1, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return SessionWindow(start, start + 3 * 60 * 1_000_000_000)


def _service(tmp_path):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    source = DurableFakeSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        _catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    return service, history, source


def test_cash_future_can_execute_through_durable_job_ledger(tmp_path):
    service, history, source = _service(tmp_path)
    job_store = HistoricalJobStore(tmp_path / "jobs.db")
    session = _session()
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc)

    _, initial_plan = service.prepare(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=start,
        end=end,
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m",
        mode="CURRENT",
    )
    assert initial_plan.requests

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=start,
        end=end,
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
        max_repair_passes=1,
        job_store=job_store,
        job_id="cash-future-test",
        run_id="run-1",
    )

    durable_id = service._durable_job_id(job_store, "cash-future-test", initial_plan)
    job = job_store.get(durable_id)
    assert job.run_id == "run-1"
    assert job.state == "completed"
    assert job.completed_chunks == len(initial_plan.requests)
    assert result.execution.results == ()
    assert result.execution.failed_request_index is None
    assert result.plan.requests == ()
    assert source.requests
    assert history.timestamps(
        source="angelone",
        instrument="NSE:3045:SBIN",
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.end_ns,
    )


def test_durable_arguments_must_be_supplied_as_a_complete_set(tmp_path):
    service, _, _ = _service(tmp_path)
    session = _session()
    kwargs = dict(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
        max_repair_passes=1,
    )

    try:
        service.acquire(**kwargs, job_id="cash-future-test")
    except ValueError as exc:
        assert str(exc) == "job_store, job_id and run_id must be supplied together"
    else:
        raise AssertionError("partial durable configuration must be rejected")
