from datetime import datetime, timezone

from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalRecord


class DurableFakeSource:
    source_name = "angelone"

    def __init__(self, fail_instrument=None):
        self.fail_instrument = fail_instrument
        self.calls = []

    def fetch(self, request):
        self.calls.append(request.instrument)
        if request.instrument == self.fail_instrument:
            raise RuntimeError("simulated interruption")
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"close": 101})


def test_durable_cash_future_failure_then_resume_replays_only_incomplete_chunk(monkeypatch):
    import app.backtesting.cash_future_historical_download as module
    from app.backtesting.cash_future_rollover_plan import CashFutureSegment
    from app.backtesting.contract_master import ContractRecord

    start = 1_000_000_000
    end = start + 60_000_000_000
    spot = HistoricalFetchRequest("angelone", "NSE:3045:SBIN", "1m", start, end)
    future = HistoricalFetchRequest("angelone", "NFO:101:SBINJAN", "1m", start, end)
    segment = CashFutureSegment(datetime(2026, 1, 1).date(), datetime(2026, 1, 1).date(), ContractRecord(
        "NFO", "SBINJAN", "101", datetime(2026, 1, 29).date(), "STOCK_FUTURE", "SBIN", 750,
    ))
    queue = CashFutureDownloadQueue(spot=spot, futures=(CashFutureSegmentDownload(segment, future),))
    monkeypatch.setattr(module, "build_rollover_download_queue", lambda **_kwargs: queue)

    catalog = HistoricalCatalog()
    contracts = object()
    status = HistoricalDownloadStatusStore()
    job_id = "durable-interruption"

    first_source = DurableFakeSource(fail_instrument=future.instrument)
    first = CashFutureHistoricalDownloadService(catalog, contracts, source=first_source, status_store=status,
                                                session_windows=lambda _: () )
    first.contract_preflight.require_complete = lambda **_kwargs: None
    report = first.run(spot_instrument=spot.instrument, exchange="NFO", underlying="SBIN",
                       start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                       timeframe="1m", mode="CURRENT", retry_attempts=1, job_id=job_id)

    assert not report.completed
    assert status.job(job_id).status == "FAILED"
    chunks = status.chunks(job_id)
    assert [chunk.status for chunk in chunks] == ["COMPLETE", "FAILED"]
    assert status.job(job_id).completed_chunks == 1
    assert status.job(job_id).failed_chunks == 1
    assert catalog.count() == 2

    second_source = DurableFakeSource()
    resumed = CashFutureHistoricalDownloadService(catalog, contracts, source=second_source, status_store=status,
                                                  session_windows=lambda _: ())
    resumed_report = resumed.run(spot_instrument=spot.instrument, exchange="NFO", underlying="SBIN",
                                 start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 end=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                                 timeframe="1m", mode="CURRENT", retry_attempts=1,
                                 job_id=job_id, resume=True)

    assert resumed_report.completed
    assert second_source.calls == [future.instrument]
    assert catalog.count() == 4
    assert status.job(job_id).status == "COMPLETE"
    assert status.job(job_id).completed_chunks == 2
    assert status.job(job_id).failed_chunks == 0
    assert status.job(job_id).inserted_records == 4
    assert status.incomplete_chunks(job_id) == ()
    status.close()
