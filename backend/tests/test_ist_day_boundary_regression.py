"""Regression coverage for IST day-boundary normalization."""

from datetime import datetime, timezone


def test_ist_day_boundary_exactly_at_midnight_ist():
    from app.notifications.common import _ist_day_start_utc_naive

    # 00:00:00 IST is exactly 18:30:00 UTC on the previous date.
    assert _ist_day_start_utc_naive(
        datetime(2026, 10, 4, 18, 30, tzinfo=timezone.utc)
    ) == datetime(2026, 10, 4, 18, 30)


def test_ist_day_boundary_one_second_before_midnight_stays_previous_day():
    from app.notifications.common import _ist_day_start_utc_naive

    # 23:59:59 IST is still the previous IST calendar day.
    assert _ist_day_start_utc_naive(
        datetime(2026, 10, 4, 18, 29, 59, tzinfo=timezone.utc)
    ) == datetime(2026, 10, 3, 18, 30)


def test_ist_day_boundary_accepts_utc_naive_input_as_utc():
    from app.notifications.common import _ist_day_start_utc_naive

    # Backend timestamps are UTC-naive. A naive value must not be interpreted
    # as local server time when determining the IST calendar day.
    assert _ist_day_start_utc_naive(
        datetime(2026, 10, 4, 18, 30)
    ) == datetime(2026, 10, 4, 18, 30)
