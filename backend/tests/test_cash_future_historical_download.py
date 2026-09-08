from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalRecord, HistoricalSyncResult
from app.backtesting.session_gap_planner import SessionWindow


class FakeSource:
    source_name = "angelone"

    def __init__(self, fail_instrument=None):
        self.fail_instrument = fail_instrument
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        if request.instrument == self.fail_instrument:
            raise RuntimeError("temporary provider failure")
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns,
                               {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns,
                                   {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1})


def _contracts():
    contracts = ContractMasterCatalog()
    contracts.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    contracts.upsert_snapshot(date(2026, 1, 30), [
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINMAR", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return contracts


def _run_kwargs():
    return dict(spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
                start=datetime(2026, 1, 29, tzinfo=timezone.utc), end=datetime(2026, 2, 2, tzinfo=timezone.utc),
                timeframe="1m", mode="CURRENT", retry_attempts=1, session_windows=lambda _: ())


def test_downloader_persists_spot_and_exact_rollover_contracts():
    catalog = HistoricalCatalog()
    source = FakeSource()
    report = CashFutureHistoricalDownloadService(catalog, _contracts(), source=source,
                                                 session_windows=lambda _: ()).run(**_run_kwargs())
    assert report.completed
    assert len(report.future_executions) == 2
    assert [x.request.instrument for x in report.queue.futures] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert catalog.count() == 6
    assert report.processed_chunks == 3


def test_partial_provider_failure_can_resume_without_duplicate_rows():
    catalog = HistoricalCatalog()
    contracts = _contracts()
    first = CashFutureHistoricalDownloadService(
        catalog, contracts, source=FakeSource(fail_instrument="NFO:101:SBINJAN"), session_windows=lambda _: ()
    ).run(**_run_kwargs())
    assert not first.completed
    assert first.spot_execution.failed_request_index is None
    assert len(first.future_executions) == 1
    assert first.future_executions[0].failed_request_index == 0
    assert catalog.count() == 2
    recovered = CashFutureHistoricalDownloadService(catalog, contracts, source=FakeSource(),
                                                    session_windows=lambda _: ()).run(**_run_kwargs())
    assert recovered.completed
    assert catalog.count() == 6


def test_complete_chunk_uses_session_completeness_to_skip():
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    request = type("R", (), {"source": "x", "instrument": "NSE:1:SBIN", "timeframe": "1m",
                              "start_ns": 0, "end_ns": 120 * 1_000_000_000})()
    catalog.ingest([HistoricalRecord("x", "NSE:1:SBIN", "1m", timestamp, {"close": 1})
                    for timestamp in (0, 60 * 1_000_000_000, 120 * 1_000_000_000)])
    service.session_windows = lambda _: (SessionWindow(0, 120 * 1_000_000_000),)
    assert service._chunk_is_complete(request)


def test_missing_middle_bar_prevents_completeness():
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    request = type("R", (), {"source": "x", "instrument": "NSE:1:SBIN", "timeframe": "1m",
                              "start_ns": 0, "end_ns": 120 * 1_000_000_000})()
    catalog.ingest([HistoricalRecord("x", "NSE:1:SBIN", "1m", timestamp, {"close": 1})
                    for timestamp in (0, 120 * 1_000_000_000)])
    service.session_windows = lambda _: (SessionWindow(0, 120 * 1_000_000_000),)
    assert not service._chunk_is_complete(request)


@dataclass
class FakeExecutor:
    accept_callbacks: list | None = None

    def __post_init__(self):
        if self.accept_callbacks is None:
            self.accept_callbacks = []

    def run(self, _source, plan, **kwargs):
        callback = kwargs.get("should_accept")
        assert callback is not None
        self.accept_callbacks.append(callback)
        request = plan.requests[0]
        assert callback(request, HistoricalSyncResult(request, 0, 0, request.end_ns)) is True
        return type("R", (), {"failed_request_index": None, "completed_chunks": 1, "skipped_chunks": 0})()


def test_run_wires_completeness_acceptance_to_spot_and_future(monkeypatch):
    import app.backtesting.cash_future_historical_download as module
    start, end = 0, 60 * 1_000_000_000
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("angelone", instrument, "1m", timestamp, {"close": 1})
                    for instrument in ("NSE:1:SBIN", "NFO:101:SBINJAN") for timestamp in (start, end)])
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=object(), executor=FakeExecutor(),
                                                  session_windows=lambda _: (SessionWindow(start, end),))
    spot = HistoricalFetchRequest("angelone", "NSE:1:SBIN", "1m", start, end)
    future = HistoricalFetchRequest("angelone", "NFO:101:SBINJAN", "1m", start, end)
    queue = CashFutureDownloadQueue(spot=spot, futures=(CashFutureSegmentDownload(segment=object(), request=future),))
    monkeypatch.setattr(module, "build_rollover_download_queue", lambda **_kwargs: queue)
    report = service.run(spot_instrument=spot.instrument, exchange="NFO", underlying="SBIN",
                         start=datetime(2026, 1, 2, tzinfo=timezone.utc), end=datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc),
                         timeframe="1m", mode="CURRENT")
    assert report.completed is True
    assert len(service.executor.accept_callbacks) == 2


def test_request_planner_keeps_chunks_non_overlapping():
    request_type = type("R", (), {"source": "angelone", "instrument": "NSE:3045:SBIN", "timeframe": "1m",
                                   "start_ns": 0, "end_ns": 14 * 24 * 60 * 60 * 1_000_000_000})
    plan = CashFutureHistoricalDownloadService._plan_for_request(request_type())
    assert len(plan.requests) == 3
    for previous, current in zip(plan.requests, plan.requests[1:]):
        assert previous.end_ns + 1 == current.start_ns


def test_resume_preserves_original_sequence_and_historical_instrument_identity():
    catalog = HistoricalCatalog()
    status = HistoricalDownloadStatusStore()
    job_id = "resume-job"
    start, end = 1_000, 2_000
    status.create_job(job_id=job_id, mode="CURRENT", timeframe="1m", spot_instrument="NSE:3045:SBIN",
                      exchange="NFO", underlying="SBIN", start_ns=start, end_ns=end, requested_chunks=3)
    for sequence, instrument, chunk_start in ((0, "NSE:3045:SBIN", start), (1, "NFO:101:SBINJAN", start),
                                               (2, "NFO:102:SBINFEB", 1_500)):
        status.upsert_chunk(DownloadChunkStatus(job_id, sequence, instrument, chunk_start, end,
                                                 "COMPLETE" if sequence == 0 else "FAILED", 1))
    source = FakeSource()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=source, status_store=status,
                                                  session_windows=lambda _: ())
    report = service.run(spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
                         start=datetime.fromtimestamp(start / 1e9, tz=timezone.utc),
                         end=datetime.fromtimestamp(end / 1e9, tz=timezone.utc), timeframe="1m", mode="CURRENT",
                         retry_attempts=1, job_id=job_id, resume=True)
    assert report.completed
    assert [request.instrument for request in source.calls] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert [chunk.sequence for chunk in status.chunks(job_id)] == [0, 1, 2]
    assert all(chunk.status == "COMPLETE" for chunk in status.chunks(job_id))
    status.close()


def test_failed_or_incomplete_chunk_is_not_reported_complete_after_retry():
    """Regression guard: strict completeness must reject a retry with missing data."""
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    request = type("R", (), {"source": "angelone", "instrument": "NSE:1:SBIN", "timeframe": "1m",
                              "start_ns": 0, "end_ns": 180 * 1_000_000_000})()
    catalog.ingest([HistoricalRecord("angelone", "NSE:1:SBIN", "1m", timestamp, {"close": 1})
                    for timestamp in (0, 60 * 1_000_000_000, 180 * 1_000_000_000)])
    service.session_windows = lambda _: (SessionWindow(0, 180 * 1_000_000_000),)
    assert service._chunk_is_complete(request) is False


def test_resume_after_future_failure_replays_only_failed_future_chunk():
    catalog = HistoricalCatalog()
    status = HistoricalDownloadStatusStore()
    contracts = _contracts()
    job_id = "future-failure-resume"
    failing = CashFutureHistoricalDownloadService(catalog, contracts,
        source=FakeSource(fail_instrument="NFO:101:SBINJAN"), status_store=status, session_windows=lambda _: ())
    first = failing.run(**_run_kwargs(), job_id=job_id)
    assert not first.completed
    assert status.job(job_id).status == "FAILED"
    assert [chunk.status for chunk in status.chunks(job_id)] == ["COMPLETE", "FAILED", "QUEUED"]

    recovering_source = FakeSource()
    recovering = CashFutureHistoricalDownloadService(catalog, contracts, source=recovering_source,
        status_store=status, session_windows=lambda _: ())
    recovered = recovering.run(spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc), end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m", mode="CURRENT", retry_attempts=1, job_id=job_id, resume=True)
    assert recovered.completed
    assert [request.instrument for request in recovering_source.calls] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert status.job(job_id).status == "COMPLETE"
    assert all(chunk.status == "COMPLETE" for chunk in status.chunks(job_id))
    assert catalog.count() == 6
    status.close()
