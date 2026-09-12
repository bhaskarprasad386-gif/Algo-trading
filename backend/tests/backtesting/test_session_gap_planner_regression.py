from app.backtesting.session_gap_planner import SessionAwareGapPlanner, SessionWindow
from app.backtesting.historical_catalog import HistoricalCatalog


def test_planner_splits_cross_session_gap_and_ignores_overnight(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    source = "angelone"
    instrument = "NSE:999:AAA"
    timeframe = "1m"
    day = 86_400_000_000_000
    minute = 60_000_000_000
    morning = 9 * 60 + 15
    session_start = day + morning * minute
    session_end = day + (15 * 60 + 29) * minute

    catalog.ingest(source, instrument, timeframe, session_start, {"close": 100})
    catalog.ingest(source, instrument, timeframe, session_start + 2 * minute, {"close": 102})
    catalog.ingest(source, instrument, timeframe, session_end, {"close": 103})

    next_day_start = 2 * day + morning * minute
    sessions = (
        SessionWindow(session_start, session_start + 2 * minute),
        SessionWindow(session_end - minute, session_end),
        SessionWindow(next_day_start, next_day_start + minute),
    )

    gaps = SessionAwareGapPlanner(catalog).plan(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        interval_ns=minute,
        sessions=sessions,
    )

    assert gaps == (
        type(gaps[0])(instrument, timeframe, session_start + minute, session_start + minute),
    )


def test_planner_does_not_create_weekend_gap(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    source = "angelone"
    instrument = "NSE:999:AAA"
    timeframe = "1m"
    friday = 5 * 86_400_000_000_000
    monday = 8 * 86_400_000_000_000
    minute = 60_000_000_000
    catalog.ingest(source, instrument, timeframe, friday, {"close": 100})
    catalog.ingest(source, instrument, timeframe, monday, {"close": 101})
    sessions = (SessionWindow(friday, friday), SessionWindow(monday, monday))

    assert SessionAwareGapPlanner(catalog).plan(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        interval_ns=minute,
        sessions=sessions,
    ) == ()
