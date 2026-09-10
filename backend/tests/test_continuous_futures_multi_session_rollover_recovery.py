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
    assert source.requests == [source.requests[0]] * len(source.requests) or len(source.requests) == 3
    assert source.requests[:3] == [source.requests[0], source.requests[0], source.requests[0]]
    assert store.chunk_state("multi-session-rollover-repair", 0)[0] == "recoverable"

    _seed_session_gaps(catalog, "NFO:JAN", (date(2026, 1, 8), date(2026, 1, 9)))
    source.allow_failed_instrument = True
    source.requests.clear()

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
    assert [request.instrument for request in source.requests] == ["NFO:FEB", "NFO:FEB"]
    assert all(date(2026, 1, 10) not in (datetime.fromtimestamp(request.start_ns / 1e9, tz=timezone.utc).date(), datetime.fromtimestamp(request.end_ns / 1e9, tz=timezone.utc).date()) for request in source.requests)
    assert store.get("multi-session-rollover-repair").state == "completed"


def test_multi_session_rollover_recovery_rejects_changed_run_id(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = _calendar()
    windows = _windows()
    source = MultiSessionSource()

    try:
        repair_continuous_futures_history_gaps(
            catalog,
            source,
            windows,
            source_name="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=calendar,
            max_request_ns=10 * INTERVAL_NS,
            job_store=store,
            job_id="run-id-repair",
            run_id="run-1",
        )
    except Exception:
        pass

    import pytest
    with pytest.raises(ValueError, match="does not match run or plan"):
        repair_continuous_futures_history_gaps(
            catalog,
            MultiSessionSource(),
            windows,
            source_name="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=calendar,
            max_request_ns=10 * INTERVAL_NS,
            job_store=store,
            job_id="run-id-repair",
            run_id="run-2",
        )
