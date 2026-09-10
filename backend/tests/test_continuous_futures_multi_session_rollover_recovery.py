from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.backtesting.continuous_futures_acquisition import repair_continuous_futures_history_gaps
from app.backtesting.fno_rollover import FNORolloverWindow, validate_futures_rollover_chain
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class MultiSessionSource:
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


def _windows() -> list[FNORolloverWindow]:
    return [
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 8), date(2026, 1, 9)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 12), date(2026, 1, 13)),
    ]


def _seed_session_gaps(catalog: HistoricalCatalog, instrument: str, days: tuple[date, ...]) -> None:
    for day in days:
        for at in (time(9, 15), time(9, 17)):
            catalog.upsert(HistoricalRecord("fake", instrument, "1m", _ns(day, at), {"close": 100.0}))


def test_multi_contract_multi_session_rollover_boundary_recovers_durably(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    source = MultiSessionSource()
    calendar = _calendar()
    windows = _windows()

    validate_futures_rollover_chain(
        windows,
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
    )
    _seed_session_gaps(catalog, "NFO:JAN", (date(2026, 1, 8), date(2026, 1, 9)))
    _seed_session_gaps(catalog, "NFO:FEB", (date(2026, 1, 12), date(2026, 1, 13)))

    first = repair_continuous_futures_history_gaps(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="multi-session-rollover-repair",
        run_id="run-1",
    )

    assert not first.completed
    assert len(first.plan.requests) == 4
    assert [(request.instrument, request.start_ns) for request in first.plan.requests] == [
        ("NFO:JAN", _ns(date(2026, 1, 8), time(9, 16))),
        ("NFO:JAN", _ns(date(2026, 1, 9), time(9, 16))),
        ("NFO:FEB", _ns(date(2026, 1, 12), time(9, 16))),
        ("NFO:FEB", _ns(date(2026, 1, 13), time(9, 16))),
    ]
    assert [request.instrument for request in source.requests] == ["NFO:JAN"] * 3
    fingerprint = store.get("multi-session-rollover-repair").plan_fingerprint
    assert store.get("multi-session-rollover-repair").state == "progress"

    # Complete both JAN session gaps externally while the durable job is paused.
    # The resume must keep the original four-request plan and only execute FEB.
    for day in (date(2026, 1, 8), date(2026, 1, 9)):
        catalog.upsert(HistoricalRecord("fake", "NFO:JAN", "1m", _ns(day, time(9, 16)), {"close": 100.0}))
    source.allow_failed_instrument = True
    before_resume = len(source.requests)

    second = repair_continuous_futures_history_gaps(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="multi-session-rollover-repair",
        run_id="run-1",
    )

    assert second.completed
    job = store.get("multi-session-rollover-repair")
    assert job.state == "completed"
    assert job.plan_fingerprint == fingerprint
    assert source.requests[:3] and all(request.instrument == "NFO:JAN" for request in source.requests[:3])
    assert [request.instrument for request in source.requests[before_resume:]] == ["NFO:FEB", "NFO:FEB"]

    # Weekend between JAN and FEB is not a missing market session and never
    # becomes a repair request. Each rollover window owns only its active dates.
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 6
    assert catalog.count(source="fake", instrument="NFO:FEB", timeframe="1m") == 6
    assert not any(
        request.instrument == "NFO:JAN" and request.start_ns >= _ns(date(2026, 1, 10), time(0, 0))
        for request in first.plan.requests
    )


def test_multi_session_rollover_recovery_rejects_run_change(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    source = MultiSessionSource()
    windows = _windows()
    _seed_session_gaps(catalog, "NFO:JAN", (date(2026, 1, 8), date(2026, 1, 9)))
    _seed_session_gaps(catalog, "NFO:FEB", (date(2026, 1, 12), date(2026, 1, 13)))

    repair_continuous_futures_history_gaps(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=_calendar(),
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="multi-session-rollover-run-id",
        run_id="run-1",
    )

    try:
        repair_continuous_futures_history_gaps(
            catalog,
            source,
            windows,
            source_name="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=_calendar(),
            max_request_ns=10 * INTERVAL_NS,
            job_store=store,
            job_id="multi-session-rollover-run-id",
            run_id="run-2",
        )
    except ValueError as exc:
        assert "does not match run or plan" in str(exc)
    else:
        raise AssertionError("run-id change must be rejected")
