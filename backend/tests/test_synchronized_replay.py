from datetime import date, datetime, timedelta

from app.scanner.synchronized_replay import synchronize_minute_bars


def test_replay_keeps_only_synchronized_minutes_and_calculates_both_gaps():
    d = date(2026, 1, 27)
    t1 = datetime(2026, 1, 5, 9, 15)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    spot = [(t1, 100.0), (t2, 101.0), (t3, 102.0)]
    current = [(t1, 102.0, d, 10), (t2, 103.0, d, 10)]
    near = [(t1, 105.0, date(2026, 2, 24), 10), (t3, 106.0, date(2026, 2, 24), 10)]

    rows = list(synchronize_minute_bars(spot, current, near))

    assert len(rows) == 1
    assert rows[0].timestamp == t1
    assert rows[0].current_gap == 2.0
    assert rows[0].near_gap == 5.0
    assert rows[0].lot_size == 10
