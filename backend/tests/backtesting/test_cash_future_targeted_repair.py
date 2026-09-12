from types import SimpleNamespace

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow


def test_missing_runs_are_collapsed_to_minimal_requests(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    service = CashFutureHistoricalDownloadService(
        catalog,
        object(),
        source=SimpleNamespace(source_name="angelone"),
        session_windows=lambda request: (SessionWindow(0, 5),),
    )
    job = SimpleNamespace(timeframe="1m")
    chunk = SimpleNamespace(instrument="NSE:1:AAA", start_ns=0, end_ns=5)
    interval = 60 * 1_000_000_000
    for timestamp in (0, 60_000_000_000, 180_000_000_000, 240_000_000_000, 300_000_000_000):
        catalog.ingest((__import__("app.backtesting.historical_catalog", fromlist=["HistoricalRecord"]).HistoricalRecord("angelone", chunk.instrument, "1m", timestamp, {"close": 100}),))
    # Session boundaries are expressed in nanoseconds; use a second service window for this test.
    service.session_windows = lambda request: (SessionWindow(0, 5 * interval),)
    requests = service._missing_requests_for_chunk(job, chunk)
    assert requests == (HistoricalFetchRequest("angelone", chunk.instrument, "1m", 120_000_000_000, 120_000_000_000),)


def test_missing_runs_helper_keeps_separated_ranges_separate():
    interval = 60 * 1_000_000_000
    assert CashFutureHistoricalDownloadService._missing_runs(
        {0, interval, 3 * interval, 4 * interval}, interval
    ) == ((0, interval), (3 * interval, 4 * interval))
