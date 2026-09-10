from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.backtesting.continuous_futures_acquisition import repair_continuous_futures_history_gaps
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class MultiContractSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
        self.fail_instrument = "NFO:JAN"
        self.allow_failed_instrument = False

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        if request.instrument == self.fail_instrument and not self.allow_failed_instrument:
            raise RuntimeError("JAN gap temporarily unavailable")
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def _calendar() -> TradingCalendar:
    return TradingCalendar(session_open=time(9, 15), session_close=time(9, 17))


def _window(token: str) -> FNORolloverWindow:
    return FNORolloverWindow("ABC", "STOCK_FUTURE", token, date(2026, 1, 9), date(2026, 1, 9))


def _seed_gap(catalog: HistoricalCatalog, instrument: str) -> None:
    for at in (time(9, 15), time(9, 17)):
        catalog.upsert(HistoricalRecord("fake", instrument, "1m", _ns(date(2026, 1, 9), at), {"close": 100.0}))


def test_multi_contract_repair_recovers_failed_gap_independently(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    source = MultiContractSource()
    calendar = _calendar()
    windows = [_window("JAN"), _window("FEB")]

    _seed_gap(catalog, "NFO:JAN")
    _seed_gap(catalog, "NFO:FEB")

    first = repair_continuous_futures_history_gaps(
        catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="multi-contract-repair", run_id="run-1",
    )

    assert not first.completed
    assert len(first.plan.requests) == 2
    assert [request.instrument for request in source.requests] == ["NFO:JAN"] * 3
    fingerprint = store.get("multi-contract-repair").plan_fingerprint
    assert store.get("multi-contract-repair").state == "progress"

    # JAN becomes available only after the durable job is paused. FEB must still
    # be executed from the original two-request plan, without rebuilding it.
    catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 9), time(9, 16)), {"close": 100.0}))
    source.allow_failed_instrument = True
    before_resume = len(source.requests)

    second = repair_continuous_futures_history_gaps(
        catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="multi-contract-repair", run_id="run-1",
    )

    assert second.completed
    assert store.get("multi-contract-repair").state == "completed"
    assert store.get("multi-contract-repair").plan_fingerprint == fingerprint
    resumed = source.requests[before_resume:]
    assert [request.instrument for request in resumed] == ["NFO:FEB"]
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 3
    assert catalog.count(source="fake", instrument="NFO:FEB", timeframe="1m") == 3


def test_multi_contract_repair_rejects_run_change_after_partial_failure(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    source = MultiContractSource()
    calendar = _calendar()
    windows = [_window("JAN"), _window("FEB")]

    _seed_gap(catalog, "NFO:JAN")
    _seed_gap(catalog, "NFO:FEB")

    repair_continuous_futures_history_gaps(
        catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
        calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
        job_id="multi-contract-run-id", run_id="run-1",
    )

    try:
        repair_continuous_futures_history_gaps(
            catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS,
            calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store,
            job_id="multi-contract-run-id", run_id="run-2",
        )
    except ValueError as exc:
        assert "does not match run or plan" in str(exc)
    else:
        raise AssertionError("run-id change must be rejected")
