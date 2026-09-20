import pytest

from app.backtesting.statistics import (
    BacktestStatistics,
    EquityPoint,
    StreamingStatisticsAccumulator,
    calculate_statistics,
)


def _points():
    return (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(86_400_000_000_000, 100_100.0, 100.0, 0.0),
        EquityPoint(172_800_000_000_000, 99_900.0, 50.0, -150.0),
        EquityPoint(259_200_000_000_000, 100_300.0, 450.0, -150.0),
    )


def _streaming(points, initial_capital):
    accumulator = StreamingStatisticsAccumulator(initial_capital)
    for point in points:
        accumulator.update(point)
    return accumulator.finalize()


def test_streaming_accumulator_matches_batch_statistics():
    points = _points()
    expected = calculate_statistics(points, 100_000.0)
    actual = _streaming(points, 100_000.0)

    assert actual == expected


def test_streaming_accumulator_preserves_same_timestamp_semantics():
    points = (
        EquityPoint(1_000, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000, 100_010.0, 10.0, 0.0),
        EquityPoint(2_000, 100_020.0, 20.0, 0.0),
    )
    assert _streaming(points, 100_000.0) == calculate_statistics(points, 100_000.0)


def test_streaming_accumulator_preserves_flat_curve_undefined_ratios():
    points = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000_000_000, 100_000.0, 0.0, 0.0),
        EquityPoint(2_000_000_000, 100_000.0, 0.0, 0.0),
    )
    result = _streaming(points, 100_000.0)

    assert result.sharpe_ratio is None
    assert result.sortino_ratio is None
    assert result.cagr == 0.0


def test_streaming_accumulator_rejects_out_of_order_points():
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    accumulator.update(EquityPoint(2, 100_000.0, 0.0, 0.0))

    with pytest.raises(ValueError, match="non-decreasing"):
        accumulator.update(EquityPoint(1, 100_001.0, 1.0, 0.0))


def test_streaming_accumulator_requires_positive_initial_capital():
    with pytest.raises(ValueError, match="initial_capital"):
        StreamingStatisticsAccumulator(0.0)


def test_streaming_accumulator_empty_finalize_matches_batch_statistics():
    actual = StreamingStatisticsAccumulator(100_000.0).finalize()
    expected = calculate_statistics((), 100_000.0)

    assert actual == expected
