from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow


class FailOnceOnSecondChunkSource:
    def __init__(self):
        self.requests = []
        self.calls = 0
        self.fail_once = True

    def fetch(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.fail_once and self.calls == 2:
            self.fail_once = False
            raise RuntimeError("temporary rollover chunk failure")
        timestamp = request.start_ns
        interval = 60 * 1_000_000_000
        while timestamp <= request.end_ns:
            yield HistoricalRecord(
                request.source, request.instrument, request.timeframe,
                timestamp, {"close": 100.0},
            )
            timestamp += interval


def test_durable_rollover_restart_resumes_only_unfinished_chunk(tmp_path):
    contracts = ContractMasterCatalog(tmp_path / "contracts.db")
    contracts.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    source = FailOnceOnSecondChunkSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion, source, contracts,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    job_path = tmp_path / "jobs.db"
    jobs = HistoricalJobStore(job_path)
    session = SessionWindow(
        int(datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 1, 29, 9, 18, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    kwargs = dict(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 9, 18, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", retry_attempts=1, max_repair_passes=1,
        job_store=jobs, job_id="rollover-restart", run_id="run-rollover-restart",
    )

    first = service.acquire(**kwargs)
    assert first.execution.failed_request_index == 1
    assert first.execution.completed_chunks == 1
    assert first.execution.skipped_request_indices == ()
    first_completed_request = source.requests[0]
    first_count = len(source.requests)
    jobs.close()

    restarted_jobs = HistoricalJobStore(job_path)
    second = service.acquire(**{**kwargs, "job_store": restarted_jobs})

    assert second.execution.failed_request_index is None
    assert second.plan.requests == ()
    assert len(source.requests) > first_count
    resumed_request = source.requests[first_count]
    assert resumed_request.start_ns == source.requests[1].start_ns
    assert resumed_request.end_ns == source.requests[1].end_ns
    assert resumed_request.instrument == source.requests[1].instrument
    assert resumed_request.start_ns != first_completed_request.start_ns
    restarted_jobs.close()
    contracts.close()
    history.close()
