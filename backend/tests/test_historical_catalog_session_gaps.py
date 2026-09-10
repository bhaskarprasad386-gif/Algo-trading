from datetime import date, datetime, time, timezone

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


def ns(day: date, hour: int, minute: int) -> int:
    return int(datetime.combine(day, time(hour, minute), tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def record(day: date, hour: int, minute: int) -> HistoricalRecord:
    return HistoricalRecord(
        source="test",
        instrument="NIFTY",
        timeframe="1m",
        timestamp_ns=ns(day, hour, minute),
        payload={"close": 100},
    )


def test_session_gaps_ignore_overnight_and_closed_dates():
    calendar = TradingCalendar(closed_dates=frozenset({date(2026, 1, 26)}))
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            [
                record(date(2026, 1, 23), 15, 30),
                record(date(2026, 1, 27), 9, 15),
                record(date(2026, 1, 27), 9, 17),
                record(date(2026, 1, 27), 9, 18),
            ]
        )

        gaps = catalog.session_gaps(
            source="test",
            instrument="NIFTY",
            timeframe="1m",
            interval_ns=60 * 1_000_000_000,
            calendar=calendar,
            start_date=date(2026, 1, 23),
            end_date=date(2026, 1, 27),
        )

        assert [(gap.start_ns, gap.end_ns) for gap in gaps] == [
            (ns(date(2026, 1, 27), 9, 16), ns(date(2026, 1, 27), 9, 16))
        ]
    finally:
        catalog.close()


def test_session_gaps_do_not_invent_tick_cadence():
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            [
                HistoricalRecord("test", "ABC", "tick", ns(date(2026, 1, 27), 9, 15), {"p": 1}),
                HistoricalRecord("test", "ABC", "tick", ns(date(2026, 1, 27), 9, 15) + 137_000, {"p": 2}),
                HistoricalRecord("test", "ABC", "tick", ns(date(2026, 1, 27), 9, 16), {"p": 3}),
            ]
        )

        assert catalog.session_gaps(
            source="test",
            instrument="ABC",
            timeframe="tick",
            interval_ns=1_000_000,
            calendar=calendar,
            start_date=date(2026, 1, 27),
            end_date=date(2026, 1, 27),
        ) == ()
    finally:
        catalog.close()
