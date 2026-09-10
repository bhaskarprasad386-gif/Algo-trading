from datetime import date, datetime, timezone

import pytest

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.cash_future_download_queue import build_rollover_download_queue
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.provider_retry import ProviderRetryPolicy
from app.backtesting.session_gap_planner import SessionWindow


class FakeHistoricalSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        timestamp = request.start_ns
        interval = 60 * 1_000_000_000
        while timestamp <= request.end_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe,
                                   timestamp, {"close": 100.0})
            timestamp += interval


class StalledHistoricalSource(FakeHistoricalSource):
    def fetch(self, request):
        self.requests.append(request)
        return iter(())


class StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"provider HTTP {status_code}")
        self.status_code = status_code


class RetryStatusHistoricalSource(FakeHistoricalSource):
    def __init__(self, status_code, fail_once):
        super().__init__()
        self.status_code = status_code
        self.fail_once = fail_once
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1 and self.fail_once:
            raise StatusError(self.status_code)
        return super().fetch(request)


class PermanentStatusHistoricalSource(FakeHistoricalSource):
    def __init__(self, status_code):
        super().__init__()
        self.status_code = status_code
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        self.requests.append(request)
        raise StatusError(self.status_code)


def _contract_catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def _window():
    start = int(datetime(2026, 1, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return SessionWindow(start, start + 3 * 60 * 1_000_000_000)


def _service(tmp_path, source=None):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    source = source or FakeHistoricalSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion, source, _contract_catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    return service, history, source


def test_acquisition_repairs_cash_and_current_next_future_without_retaining_results(tmp_path):
    service, history, source = _service(tmp_path)
    session = _window()
    future_sessions = {"NFO:101:SBINJAN": (session,), "NFO:102:SBINFEB": (session,)}

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions=future_sessions,
        timeframe="1m", mode="BOTH", retry_attempts=1,
    )

    assert result.execution.failed_request_index is None
    assert result.execution.results == ()
    assert result.execution.completed_chunks == result.execution.processed_chunks
    assert result.plan.requests == ()
    assert source.requests
    assert history.timestamps(source="angelone", instrument="NSE:3045:SBIN", timeframe="1m",
                              start_ns=session.start_ns, end_ns=session.end_ns)


def test_acquisition_exposes_only_coverage_snapshots_not_raw_download_results(tmp_path):
    service, _, _ = _service(tmp_path)
    session = _window()
    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,), "NFO:102:SBINFEB": (session,)},
        timeframe="1m", mode="BOTH", retry_attempts=1,
    )

    assert len(result.progress) >= 2
    assert not result.progress[0].complete
    assert result.progress[-1].complete
    assert all(not hasattr(snapshot, "results") for snapshot in result.progress)
    assert result.execution.results == ()


def test_progress_callback_reports_replanned_pending_chunks_without_raw_rows(tmp_path):
    service, _, _ = _service(tmp_path)
    session = _window()
    events = []
    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,), "NFO:102:SBINFEB": (session,)},
        timeframe="1m", mode="BOTH", retry_attempts=1,
        on_progress=events.append,
    )

    assert len(events) == len(result.progress)
    assert events[0].pass_index == 0
    assert events[0].completed_chunks == 0
    assert events[-1].coverage.complete
    assert events[-1].pending_chunks == len(result.plan.requests)
    assert events[-1].pending_chunks == 0
    assert not hasattr(events[-1], "results")


def test_bounded_repair_stops_when_source_makes_no_progress(tmp_path):
    source = StalledHistoricalSource()
    service, history, _ = _service(tmp_path, source)
    session = _window()
    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", retry_attempts=1, max_repair_passes=2,
    )
    assert result.execution.results == ()
    assert result.execution.failed_request_index is None
    assert len(source.requests) == 4
    assert [request.instrument for request in source.requests].count("NFO:101:SBINJAN") == 2
    assert [request.instrument for request in source.requests].count("NSE:3045:SBIN") == 2
    assert result.coverage.incomplete_chunks
    assert not history.timestamps(source="angelone", instrument="NSE:3045:SBIN", timeframe="1m",
                                  start_ns=session.start_ns, end_ns=session.end_ns)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_acquisition_forwards_retry_policy_to_provider_status_errors(tmp_path, status_code):
    source = RetryStatusHistoricalSource(status_code, fail_once=True)
    service, history, _ = _service(tmp_path, source)
    session = _window()
    sleeps = []
    policy = ProviderRetryPolicy(
        base_delay_seconds=0.25, max_delay_seconds=1.0, jitter_ratio=0,
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", retry_attempts=2, retry_policy=policy,
        max_repair_passes=1,
    )

    assert result.execution.failed_request_index is None
    assert source.calls == 2
    assert sleeps == [0.25]
    assert result.plan.requests == ()
    assert history.timestamps(source="angelone", instrument="NSE:3045:SBIN", timeframe="1m",
                              start_ns=session.start_ns, end_ns=session.end_ns)


def test_acquisition_does_not_retry_permanent_http_status(tmp_path):
    source = PermanentStatusHistoricalSource(400)
    service, _, _ = _service(tmp_path, source)
    session = _window()
    sleeps = []
    policy = ProviderRetryPolicy(
        base_delay_seconds=0.25, jitter_ratio=0,
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", retry_attempts=3, retry_policy=policy,
        max_repair_passes=1,
    )

    assert result.execution.failed_request_index == 0
    assert source.calls == 1
    assert sleeps == []
    assert result.plan.requests


def test_prepare_is_empty_after_all_expected_timestamps_are_stored(tmp_path):
    service, history, _ = _service(tmp_path)
    session = _window()
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc)
    queue = build_rollover_download_queue(
        catalog=service.contract_master, spot_instrument="NSE:3045:SBIN",
        exchange="NFO", underlying="SBIN", start=start, end=end,
        timeframe="1m", mode="BOTH",
    )
    for request in queue.all_requests:
        timestamp = session.start_ns
        while timestamp <= session.end_ns:
            history.ingest([HistoricalRecord(request.source, request.instrument,
                                              request.timeframe, timestamp, {"close": 100.0})])
            timestamp += 60 * 1_000_000_000

    _, plan = service.prepare(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=start, end=end, spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,), "NFO:102:SBINFEB": (session,)},
        mode="BOTH",
    )
    assert plan.requests == ()


def test_queue_uses_exact_current_contract_before_and_after_expiry(tmp_path):
    catalog = ContractMasterCatalog(tmp_path / "contracts.db")
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINMAR", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])

    queue = build_rollover_download_queue(
        catalog=catalog,
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 1, 30, 9, 15, tzinfo=timezone.utc),
        timeframe="1m",
        mode="CURRENT",
    )

    assert [item.request.instrument for item in queue.futures] == [
        "NFO:101:SBINJAN", "NFO:102:SBINFEB"
    ]
    assert queue.futures[0].segment.end == date(2026, 1, 29)
    assert queue.futures[1].segment.start == date(2026, 1, 30)
    assert queue.futures[0].request.end_ns < queue.futures[1].request.start_ns


def test_both_mode_keeps_current_and_near_rollover_legs_independent(tmp_path):
    catalog = ContractMasterCatalog(tmp_path / "contracts.db")
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINMAR", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])

    queue = build_rollover_download_queue(
        catalog=catalog, spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 1, 30, 9, 15, tzinfo=timezone.utc),
        timeframe="1m", mode="BOTH",
    )

    assert [item.request.instrument for item in queue.futures] == [
        "NFO:101:SBINJAN", "NFO:102:SBINFEB", "NFO:102:SBINFEB", "NFO:103:SBINMAR",
    ]
    assert [item.segment.future.token for item in queue.futures] == ["101", "102", "102", "103"]


def test_gap_repair_crosses_expiry_with_exact_historical_tokens(tmp_path):
    service, history, source = _service(tmp_path)
    jan_session = SessionWindow(
        int(datetime(2026, 1, 29, 3, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 1, 29, 3, 47, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    feb_session = SessionWindow(
        int(datetime(2026, 1, 30, 3, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 1, 30, 3, 47, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    start = datetime(2026, 1, 29, 3, 45, tzinfo=timezone.utc)
    end = datetime(2026, 1, 30, 3, 47, tzinfo=timezone.utc)

    for timestamp in range(jan_session.start_ns, jan_session.end_ns + 1, 60 * 1_000_000_000):
        history.ingest([HistoricalRecord("angelone", "NFO:101:SBINJAN", "1m", timestamp, {"close": 100.0})])

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=start, end=end, spot_sessions=(jan_session, feb_session),
        future_sessions={"NFO:101:SBINJAN": (jan_session,), "NFO:102:SBINFEB": (feb_session,)},
        timeframe="1m", mode="CURRENT", retry_attempts=1,
    )

    assert result.execution.failed_request_index is None
    assert result.execution.results == ()
    future_requests = [r for r in source.requests if r.instrument.startswith("NFO:")]
    assert future_requests
    assert {r.instrument for r in future_requests} == {"NFO:102:SBINFEB"}
    assert all(r.start_ns >= feb_session.start_ns for r in future_requests)
    assert all(r.end_ns <= feb_session.end_ns for r in future_requests)
