from datetime import datetime, timedelta

from app.backtesting.cash_future_replay_routes import _available_replay_intervals, _replay_deltas


class Point:
    def __init__(self, timestamp):
        self.timestamp = timestamp


def test_replay_preserves_source_cadence_and_does_not_claim_finer_data():
    start = datetime(2026, 9, 2, 9, 15)
    points = [Point(start), Point(start + timedelta(minutes=1)), Point(start + timedelta(minutes=2))]
    assert _replay_deltas(points) == [60.0, 60.0]
    assert _available_replay_intervals(points) == ["1m", "5m", "15m", "30m", "1h"]


def test_replay_allows_one_second_only_when_source_really_has_one_second_cadence():
    start = datetime(2026, 9, 2, 9, 15)
    points = [Point(start), Point(start + timedelta(seconds=1)), Point(start + timedelta(seconds=2))]
    assert _replay_deltas(points) == [1.0, 1.0]
    assert _available_replay_intervals(points) == ["1s", "30s", "1m", "5m", "15m", "30m", "1h"]


def test_replay_single_observation_has_no_fabricated_subminute_intervals():
    assert _available_replay_intervals([Point(datetime(2026, 9, 2, 9, 15))]) == ["1m", "5m", "15m", "30m", "1h"]
