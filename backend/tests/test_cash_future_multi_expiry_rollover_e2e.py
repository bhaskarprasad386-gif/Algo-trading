from datetime import date, time

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


class CompleteSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"close": 100.0},
            )


def test_multi_expiry_rollover_downloads_each_contract_and_persists_to_sqlite(tmp_path):
    catalog_path = tmp_path / "history.sqlite"
    catalog = HistoricalCatalog(catalog_path)
    source = CompleteSource()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "102", date(2026, 2, 26), date(2026, 2, 26)),
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "103", date(2026, 3, 26), date(2026, 3, 26)),
    )

    result = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )

    assert result.completed
    assert [request.instrument for request in source.requests] == [
        "NFO:101",
        "NFO:102",
        "NFO:103",
    ]
    for token in ("101", "102", "103"):
        assert catalog.count(source="fake", instrument=f"NFO:{token}", timeframe="1m") == 2
    catalog.close()

    reopened = HistoricalCatalog(catalog_path)
    for token in ("101", "102", "103"):
        assert reopened.count(source="fake", instrument=f"NFO:{token}", timeframe="1m") == 2
    reopened.close()
