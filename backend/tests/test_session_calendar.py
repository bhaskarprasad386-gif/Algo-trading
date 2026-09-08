from datetime import date, datetime

from app.market_data.session_calendar import trading_session_ranges


def test_weekend_is_not_a_missing_trading_session():
    ranges = trading_session_ranges(datetime(2025, 1, 3, 9, 15), datetime(2025, 1, 6, 15, 30))

    assert ranges == [
        (datetime(2025, 1, 3, 9, 15), datetime(2025, 1, 3, 15, 30)),
        (datetime(2025, 1, 6, 9, 15), datetime(2025, 1, 6, 15, 30)),
    ]


def test_holiday_can_be_excluded_without_changing_storage_layer():
    holiday = date(2025, 1, 6)
    ranges = trading_session_ranges(
        datetime(2025, 1, 3, 9, 15),
        datetime(2025, 1, 7, 15, 30),
        holidays={holiday},
    )

    assert [item[0].date() for item in ranges] == [date(2025, 1, 3), date(2025, 1, 7)]
