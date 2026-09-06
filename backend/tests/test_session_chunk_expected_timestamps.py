from app.backtesting.session_chunk_completeness import SessionChunkCompleteness
from app.backtesting.session_gap_planner import SessionWindow


def test_expected_timestamps_enumerates_each_session_without_cross_session_bars():
    expected = SessionChunkCompleteness.expected_timestamps(
        (SessionWindow(0, 120), SessionWindow(300, 360)), 60
    )
    assert expected == {0, 60, 120, 300, 360}


def test_expected_timestamps_rejects_invalid_interval():
    try:
        SessionChunkCompleteness.expected_timestamps((SessionWindow(0, 60),), 0)
    except ValueError as exc:
        assert str(exc) == "interval_ns must be positive"
    else:
        raise AssertionError("expected ValueError")
