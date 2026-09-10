from datetime import date, datetime, time, timezone

from app.backtesting.catalog_continuous_futures_pipeline import run_continuous_futures_history_pipeline
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.trading_calendar import TradingCalendar


class FakeSource:
    source_name = "fake"

    def __init__(self):
        self.requests = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        yield HistoricalRecord(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            timestamp_ns=request.start_ns,
            payload={"close": 100.0, "token": request.instrument.split(":", 1)[1]},
        )


def _contract(token: str, expiry: date, snapshot: date) -> ContractRecord:
    return ContractRecord(
        exchange="NFO",
        symbol=f"ABC{token}FUT",
        token=token,
        expiry=expiry,
        instrument_type="STOCK_FUTURE",
        underlying="ABC",
        lot_size=1,
        snapshot_date=snapshot,
    )


def _ns(day: date, hour: int = 10, minute: int = 0) -> int:
    return int(datetime.combine(day, time(hour, minute), tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_pipeline_acquires_only_active_session_ranges_and_projects_catalog():
    contracts = ContractMasterCatalog()
    history = HistoricalCatalog()
    source = FakeSource()
    calendar = TradingCalendar(session_open=time(10, 0), session_close=time(10, 2))
    try:
        contracts.upsert_snapshot(date(2026, 1, 1), (
            _contract("JAN", date(2026, 1, 2), date(2026, 1, 1)),
            _contract("FEB", date(2026, 1, 5), date(2026, 1, 1)),
        ))

        result = run_continuous_futures_history_pipeline(
            contracts, history, source,
            underlying="ABC", start_date=date(2026, 1, 2), end_date=date(2026, 1, 5),
            timeframe="1m", interval_ns=60_000_000_000, calendar=calendar,
            max_request_ns=120_000_000_000,
        )

        assert [window.contract_token for window in result.windows] == ["JAN", "FEB"]
        assert all(request.instrument in {"NFO:JAN", "NFO:FEB"} for request in source.requests)
        assert all(request.start_ns <= request.end_ns for request in source.requests)
        assert not any(request.instrument == "NFO:JAN" and request.start_ns > _ns(date(2026, 1, 2), 10, 2) for request in source.requests)
        assert all(item.contract_token in {"JAN", "FEB"} for item in result.series)
        assert result.acquisition.completed
    finally:
        contracts.close()
        history.close()


def test_pipeline_is_restart_safe_for_complete_chunks():
    contracts = ContractMasterCatalog()
    history = HistoricalCatalog()
    source = FakeSource()
    calendar = TradingCalendar(session_open=time(10, 0), session_close=time(10, 0))
    try:
        contracts.upsert_snapshot(date(2026, 1, 1), (
            _contract("JAN", date(2026, 1, 2), date(2026, 1, 1)),
        ))
        first = run_continuous_futures_history_pipeline(
            contracts, history, source,
            underlying="ABC", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2),
            timeframe="1m", interval_ns=60_000_000_000, calendar=calendar,
            max_request_ns=60_000_000_000,
        )
        request_count = len(source.requests)
        second = run_continuous_futures_history_pipeline(
            contracts, history, source,
            underlying="ABC", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2),
            timeframe="1m", interval_ns=60_000_000_000, calendar=calendar,
            max_request_ns=60_000_000_000,
        )
        assert first.series == second.series
        assert len(source.requests) == request_count
    finally:
        contracts.close()
        history.close()
