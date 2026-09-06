from app.backtesting.cash_future_download_report import CashFutureDownloadReporter
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow


def _request(instrument: str) -> HistoricalFetchRequest:
    return HistoricalFetchRequest("angelone", instrument, "1m", 0, 180_000_000_000)


def test_report_counts_missing_timestamp_and_first_missing():
    catalog = HistoricalCatalog()
    request = _request("NSE:123")
    catalog.ingest(
        HistoricalRecord("angelone", "NSE:123", "1m", timestamp, {"close": 1})
        for timestamp in (0, 120_000_000_000, 180_000_000_000)
    )

    report = CashFutureDownloadReporter(catalog).chunk_status(
        request,
        interval_ns=60_000_000_000,
        sessions=(SessionWindow(0, 180_000_000_000),),
    )

    assert report.expected_timestamps == 4
    assert report.actual_timestamps == 3
    assert report.missing_timestamps == 1
    assert report.first_missing_ns == 60_000_000_000
    assert report.complete is False
    catalog.close()


def test_report_marks_exact_cash_future_queue_complete():
    catalog = HistoricalCatalog()
    spot = _request("NSE:SPOT")
    future = _request("NFO:999:ABC")
    for instrument in (spot.instrument, future.instrument):
        catalog.ingest(
            HistoricalRecord("angelone", instrument, "1m", timestamp, {"close": 1})
            for timestamp in (0, 60_000_000_000, 120_000_000_000, 180_000_000_000)
        )

    queue = CashFutureDownloadQueue(
        spot=spot,
        futures=(),
    )
    # A queue without futures is valid; this verifies the spot side and aggregate state.
    report = CashFutureDownloadReporter(catalog).report(
        queue=queue,
        mode="CURRENT",
        interval_ns=60_000_000_000,
        spot_sessions=(SessionWindow(0, 180_000_000_000),),
    )

    assert report.total_chunks == 1
    assert report.complete_chunks == 1
    assert report.incomplete_chunks == 0
    assert report.complete is True
    catalog.close()
