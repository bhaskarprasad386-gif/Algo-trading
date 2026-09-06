from datetime import date

from app.backtesting.nse_2026_holidays import NSE_FNO_TRADING_HOLIDAYS_2026


def test_nse_2026_fno_holiday_set_matches_official_circular_dates():
    assert len(NSE_FNO_TRADING_HOLIDAYS_2026) == 15
    assert date(2026, 1, 26) in NSE_FNO_TRADING_HOLIDAYS_2026
    assert date(2026, 11, 24) in NSE_FNO_TRADING_HOLIDAYS_2026
    assert date(2026, 12, 25) in NSE_FNO_TRADING_HOLIDAYS_2026


def test_weekends_are_not_duplicated_as_holidays():
    assert all(day.weekday() < 5 for day in NSE_FNO_TRADING_HOLIDAYS_2026)
