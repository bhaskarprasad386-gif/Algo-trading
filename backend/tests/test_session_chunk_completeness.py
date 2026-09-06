from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from app.backtesting.session_gap_planner import SessionWindow


def test_missing_middle_bar_is_not_complete():
    catalog = HistoricalCatalog()
    records = [
        HistoricalRecord("x", "i", "1m", 0, {"close": 1}),
        HistoricalRecord("x", "i", "1m", 60, {"close": 1}),
    ]
    catalog.ingest(records)
    checker = SessionChunkCompleteness(catalog)
    assert not checker.is_complete(
        source="x", instrument="i", timeframe="1m", interval_ns=60,
        chunk=SessionChunk(0, 120), sessions=(SessionWindow(0, 120),),
    )


def test_complete_session_is_complete():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("x", "i", "1m", timestamp, {"close": 1})
        for timestamp in (0, 60, 120)
    ])
    checker = SessionChunkCompleteness(catalog)
    assert checker.is_complete(
        source="x", instrument="i", timeframe="1m", interval_ns=60,
        chunk=SessionChunk(0, 120), sessions=(SessionWindow(0, 120),),
    )


def test_non_session_time_is_ignored():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("x", "i", "1m", timestamp, {"close": 1})
        for timestamp in (0, 60)
    ])
    checker = SessionChunkCompleteness(catalog)
    assert checker.is_complete(
        source="x", instrument="i", timeframe="1m", interval_ns=60,
        chunk=SessionChunk(0, 180),
        sessions=(SessionWindow(0, 60), SessionWindow(180, 180)),
    ) is False


def test_empty_chunk_without_expected_session_is_complete():
    catalog = HistoricalCatalog()
    checker = SessionChunkCompleteness(catalog)
    assert checker.is_complete(
        source="x", instrument="i", timeframe="1m", interval_ns=60,
        chunk=SessionChunk(0, 180), sessions=(),
    )
