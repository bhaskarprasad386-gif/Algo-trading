
def _enable(db_session, user_id):
    db_session.add(
        GlobalPaperSetting(
            user_id=user_id,
            enabled=True,
            paper_amount=10_000_000,
            emergency_stop=False,
        )
    )
    db_session.commit()


def test_ist_day_start_uses_current_ist_calendar_day():
    from datetime import datetime, timezone
    from app.notifications.common import _ist_day_start_utc_naive

    # 2026-10-04 18:40 UTC is 2026-10-05 00:10 IST, so the IST day starts
    # at 2026-10-04 18:30 UTC.
    now = datetime(2026, 10, 4, 18, 40, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(now) == datetime(2026, 10, 4, 18, 30)


def test_ist_day_start_before_ist_midnight_stays_on_previous_utc_date():
    from datetime import datetime, timezone
    from app.notifications.common import _ist_day_start_utc_naive

    # 2026-10-04 17:59 UTC is 2026-10-04 23:29 IST.
    now = datetime(2026, 10, 4, 17, 59, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(now) == datetime(2026, 10, 3, 18, 30)


