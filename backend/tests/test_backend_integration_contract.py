from datetime import datetime, timezone


def test_ist_day_start_uses_current_ist_calendar_day():
    from app.notifications.common import _ist_day_start_utc_naive

    # 2026-10-04 18:40 UTC is 2026-10-05 00:10 IST.
    now = datetime(2026, 10, 4, 18, 40, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(now) == datetime(2026, 10, 4, 18, 30)


def test_ist_day_start_before_ist_midnight_stays_on_previous_utc_date():
    from app.notifications.common import _ist_day_start_utc_naive

    # 2026-10-04 17:59 UTC is 2026-10-04 23:29 IST.
    now = datetime(2026, 10, 4, 17, 59, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(now) == datetime(2026, 10, 3, 18, 30)
