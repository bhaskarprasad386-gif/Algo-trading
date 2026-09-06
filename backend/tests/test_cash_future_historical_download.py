from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalRecord, HistoricalSyncResult
from app.backtesting.session_gap_planner import SessionWindow


class FakeSource:
    def __init__(self, fail_instrument=None):
        self.fail_instrument = fail_instrument
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        if request.instrument == self.fail_instrument:
            raise RuntimeError("temporary provider failure")
        yield HistoricalRecord(
            request.source,
            request.instrument,
            request.timeframe,
            request.start_ns,
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        )


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
    return dict(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
        session_windows=lambda _: (),
    )


def test_downloader_persists_spot_and_exact_rollover_contracts():
    catalog = HistoricalCatalog()
    source = FakeSource()
    report = CashFutureHistoricalDownloadService(catalog, _contracts(), source=source).run(**_run_kwargs())
    assert report.completed
    assert len(report.future_executions) == 2
    assert [x.request.instrument for x in report.queue.futures] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert catalog.count() == 3
    assert report.processed_chunks == 3


def test_partial_provider_failure_can_resume_without_duplicate_rows():
    catalog = HistoricalCatalog()
    contracts = _contracts()
    first = CashFutureHistoricalDownloadService(
        catalog, contracts, source=FakeSource(fail_instrument="NFO:101:SBINJAN"), session_windows=lambda _: ()
    ).run(**{k: v for k, v in _run_kwargs().items() if k != "session_windows"})
    assert not first.completed
    assert first.spot_execution.failed_request_index is None
    assert len(first.future_executions) == 1
    assert first.future_executions[0].failed_request_index == 0
    assert catalog.count() == 1

    recovered = CashFutureHistoricalDownloadService(catalog, contracts, source=FakeSource(), session_windows=lambda _: ()).run(
        **{k: v for k, v in _run_kwargs().items() if k != "session_windows"}
    )
    assert recovered.completed
    assert catalog.count() == 3


def test_complete_chunk_uses_session_completeness_to_skip():
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    request = type("R", (), {
        "source": "x", "instrument": "NSE:1:SBIN", "timeframe": "1m",
        "start_ns": 0, "end_ns": 120 * 1_000_000_000,
    })()
    catalog.ingest([
        HistoricalRecord("x", "NSE:1:SBIN", "1m", timestamp, {"close": 1})
        for timestamp in (0, 60 * 1_000_000_000, 120 * 1_000_000_000)
    ])
    service.session_windows = lambda _: (SessionWindow(0, 120 * 1_000_000_000),)
    assert service._should_skip_chunk(request)
    assert service._should_accept_chunk(request, None)


def test_missing_middle_bar_prevents_skip_and_acceptance():
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    request = type("R", (), {
        "source": "x", "instrument": "NSE:1:SBIN", "timeframe": "1m",
        "start_ns": 0, "end_ns": 120 * 1_000_000_000,
    })()
    catalog.ingest([
        HistoricalRecord("x", "NSE:1:SBIN", "1m", timestamp, {"close": 1})
        for timestamp in (0, 120 * 1_000_000_000)
    ])
    service.session_windows = lambda _: (SessionWindow(0, 120 * 1_000_000_000),)
    assert not service._should_skip_chunk(request)
    assert not service._should_accept_chunk(request, None)


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

    start = 0
    end = 60 * 1_000_000_000
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("angelone", "NSE:1:SBIN", "1m", start, {"close": 1}),
        HistoricalRecord("angelone", "NSE:1:SBIN", "1m", end, {"close": 2}),
        HistoricalRecord("angelone", "NFO:101:SBINJAN", "1m", start, {"close": 1}),
        HistoricalRecord("angelone", "NFO:101:SBINJAN", "1m", end, {"close": 2}),
    ])
    service = CashFutureHistoricalDownloadService(
        catalog, _contracts(), source=object(), executor=FakeExecutor(),
        session_windows=lambda _: (SessionWindow(start, end),),
    )
    spot = HistoricalFetchRequest("angelone", "NSE:1:SBIN", "1m", start, end)
    future = HistoricalFetchRequest("angelone", "NFO:101:SBINJAN", "1m", start, end)
    queue = CashFutureDownloadQueue(
        spot=spot,
        futures=(CashFutureSegmentDownload(segment=object(), request=future),),
    )
    monkeypatch.setattr(module, "build_rollover_download_queue", lambda **_kwargs: queue)

    report = service.run(
        spot_instrument=spot.instrument,
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 2, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc),
        timeframe="1m",
        mode="CURRENT",
    )

    assert report.completed is True
    assert len(service.executor.accept_callbacks) == 2


def test_request_planner_keeps_chunks_non_overlapping():
    request_type = type(
        "R", (), {
            "source": "angelone", "instrument": "NSE:3045:SBIN", "timeframe": "1m",
            "start_ns": 0, "end_ns": 14 * 24 * 60 * 60 * 1_000_000_000,
        }
    )
    plan = CashFutureHistoricalDownloadService._plan_for_request(request_type())
    assert len(plan.requests) == 3
    for previous, current in zip(plan.requests, plan.requests[1:]):
        assert previous.end_ns + 1 == current.start_ns
