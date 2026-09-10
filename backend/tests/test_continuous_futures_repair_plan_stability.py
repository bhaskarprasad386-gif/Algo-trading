from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from app.backtesting.continuous_futures_acquisition import repair_continuous_futures_history_gaps
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class RepairSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
        self.fail_first = True

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("targeted gap temporarily unavailable")
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def _calendar() -> TradingCalendar:
    return TradingCalendar(session_open=time(9, 15), session_close=time(9, 17))


def _window() -> FNORolloverWindow:
    return FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 9), date(2026, 1, 9))


def _seed_complete_except_two_gaps(catalog: HistoricalCatalog) -> None:
    for at in (time(9, 15), time(9, 16), time(9, 17)):
        if at != time(9, 16):
            catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), at), {"close": 100.0}))
    # A second gap is created by using a second session on the same contract.
    for at in (time(9, 15), time(9, 16)):
        catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 12), at), {"close": 100.0}))


def test_repair_resume_reuses_original_plan_when_catalog_changes(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(
        session_open=time(9, 15),
        session_close=time(9, 17),
    )
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 9), date(2026, 1, 12))
    source = RepairSource()

    # Seed Friday with one missing candle and Monday with one missing candle.
    for at in (time(9, 15), time(9, 17)):
        catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), at), {"close": 100.0}))
    for at in (time(9, 15), time(9, 17)):
        catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 12), at), {"close": 100.0}))

    first = repair_continuous_futures_history_gaps(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        executor=None,
        job_store=store,
        job_id="repair-plan-job",
        run_id="run-1",
    )

    assert not first.completed
    assert store.get("repair-plan-job").state == "progress"
    fingerprint = store.get("repair-plan-job").plan_fingerprint
    assert len(first.plan.requests) == 2
    assert len(source.requests) == 1
    failed_date = date(2026, 1, 9)
    assert datetime.fromtimestamp(source.requests[0].start_ns / 1_000_000_000, tz=timezone.utc).date() == failed_date

    # External catalog progress changes the live gap set: the first gap is filled
    # before resume. The durable repair job must still use its original plan.
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 16)), {"close": 100.0}))
    before_resume = len(source.requests)

    second = repair_continuous_futures_history_gaps(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="repair-plan-job",
        run_id="run-1",
    )

    assert second.completed
    assert store.get("repair-plan-job").state == "completed"
    assert store.get("repair-plan-job").plan_fingerprint == fingerprint
    resumed = source.requests[before_resume:]
    assert len(resumed) == 1
    assert resumed[0].start_ns == _ns(date(2026, 1, 12), time(9, 16))
    assert resumed[0].end_ns == _ns(date(2026, 1, 12), time(9, 16))
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 6


def test_repair_resume_does_not_redownload_completed_gap(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = _calendar()
    window = _window()
    source = RepairSource()

    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 15)), {"close": 100.0}))
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 17)), {"close": 100.0}))

    first = repair_continuous_futures_history_gaps(
        catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="repair-completed-gap-job", run_id="run-1",
    )
    assert not first.completed
    assert len(source.requests) == 1

    # Fill the targeted gap outside the executor, then resume. No provider call
    # should be made because the persisted repair request is already complete.
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 16)), {"close": 100.0}))
    before = len(source.requests)
    second = repair_continuous_futures_history_gaps(
        catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="repair-completed-gap-job", run_id="run-1",
    )
    assert second.completed
    assert len(source.requests) == before
    assert store.get("repair-completed-gap-job").state == "completed"


def test_repair_existing_job_rejects_different_run_id(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = _calendar()
    window = _window()
    source = RepairSource()
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 15)), {"close": 100.0}))
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 17)), {"close": 100.0}))

    repair_continuous_futures_history_gaps(
        catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="repair-run-id-job", run_id="run-1",
    )

    with pytest.raises(ValueError, match="does not match run or plan"):
        repair_continuous_futures_history_gaps(
            catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
            calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
            job_id="repair-run-id-job", run_id="run-2",
        )
