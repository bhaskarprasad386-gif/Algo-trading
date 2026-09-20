import math

import pytest

from app.backtesting.statistics import EquityPoint, calculate_statistics


def test_realized_and_unrealized_are_kept_separate():
    points = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000_000_000, 100_010.0, 10.0, 0.0),
        EquityPoint(2_000_000_000, 100_020.0, 10.0, 10.0),
    )
    result = calculate_statistics(points, 100_000.0)
    assert result.net_pnl == pytest.approx(20.0)
    assert result.total_return == pytest.approx(0.0002)
    assert result.max_drawdown == pytest.approx(0.0)


def test_drawdown_uses_complete_equity_curve_not_only_trade_results():
    points = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000_000_000, 101_000.0, 1_000.0, 0.0),
        EquityPoint(2_000_000_000, 95_000.0, 1_000.0, -6_000.0),
        EquityPoint(3_000_000_000, 100_000.0, 1_000.0, -1_000.0),
    )
    result = calculate_statistics(points, 100_000.0)
    assert result.max_drawdown == pytest.approx(6_000 / 101_000)


def test_zero_variance_and_zero_downside_are_explicitly_undefined():
    flat = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000_000_000, 100_000.0, 0.0, 0.0),
        EquityPoint(2_000_000_000, 100_000.0, 0.0, 0.0),
    )
    result = calculate_statistics(flat, 100_000.0)
    assert result.sharpe_ratio is None
    assert result.sortino_ratio is None


def test_millisecond_spacing_changes_annualized_ratio():
    slow = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(86_400_000_000_000, 100_100.0, 100.0, 0.0),
        EquityPoint(172_800_000_000_000, 100_200.0, 200.0, 0.0),
    )
    fast = (
        EquityPoint(0, 100_000.0, 0.0, 0.0),
        EquityPoint(1_000_000, 100_100.0, 100.0, 0.0),
        EquityPoint(2_000_000, 100_200.0, 200.0, 0.0),
    )
    slow_result = calculate_statistics(slow, 100_000.0)
    fast_result = calculate_statistics(fast, 100_000.0)
    assert slow_result.sharpe_ratio is not None
    assert fast_result.sharpe_ratio is not None
    assert fast_result.sharpe_ratio > slow_result.sharpe_ratio


def test_non_monotonic_equity_timestamps_are_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        calculate_statistics(
            (EquityPoint(2, 100_000.0, 0.0, 0.0), EquityPoint(1, 100_001.0, 1.0, 0.0)),
            100_000.0,
        )


def test_same_timestamp_equity_points_are_allowed_for_multi_instrument_events():
    result = calculate_statistics(
        (
            EquityPoint(1_000, 100_000.0, 0.0, 0.0),
            EquityPoint(1_000, 100_010.0, 10.0, 0.0),
            EquityPoint(2_000, 100_020.0, 20.0, 0.0),
        ),
        100_000.0,
    )
    assert result.net_pnl == pytest.approx(20.0)
    assert result.total_return == pytest.approx(0.0002)
