from datetime import datetime, timezone

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from app.backtesting.nse_session_calendars import nse_session_windows


def _request(start_ns: int, end_ns: int):
    return HistoricalFetchRequest("angelone", "NSE:1:AAA", "1m", start_ns, end_ns)


def test_weekend_is_not_a_missing_market_session():
    start = datetime(2026, 1, 9, 3, 45, tzinfo=timezone.utc)
    end = datetime(2026, 1, 12, 9, 59, tzinfo=timezone.utc)
    request = _request(int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000))

    sessions = nse_session_windows(request)

    assert len(sessions) == 2


def test_session_completeness_ignores_overnight_and_weekend_gap(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "data.db"))
    try:
        service = CashFutureHistoricalDownloadService(catalog, object())
        start = datetime(2026, 1, 9, 3, 45, tzinfo=timezone.utc)
        end = datetime(2026, 1, 12, 9, 59, tzinfo=timezone.utc)
        request = _request(int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000))
        sessions = tuple(nse_session_windows(request))
        interval = 60 * 1_000_000_000
        expected = SessionChunkCompleteness.expected_timestamps(sessions, interval)
        assert len(expected) == 2 * 375

        for timestamp_ns in expected:
            catalog.ingest(
                source="angelone",
                instrument="NSE:1:AAA",
                timeframe="1m",
                timestamp_ns=timestamp_ns,
                payload={"close": 100.0},
            )

        assert service._chunk_is_complete(request)
        assert service._chunk_is_complete(request, result=None)
    finally:
        catalog.close()
