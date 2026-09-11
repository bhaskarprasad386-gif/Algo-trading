from datetime import date

import pytest

from app.backtesting.daily_gap import (
    DailyGapObservation,
    build_daily_gap_observations,
    top_daily_gap,
)


def test_top_daily_gap_uses_absolute_gap_times_historical_lot_size() -> None:
    rows = (
        DailyGapObservation(date(2026, 9, 10), "AAA", 100.0, 104.0, 106.0, 99.0, 103.0, 100),
        DailyGapObservation(date(2026, 9, 10), "BBB", 200.0, 194.0, 201.0, 190.0, 198.0, 200),
        DailyGapObservation(date(2026, 9, 10), "CCC", 50.0, 51.0, 52.0, 49.0, 50.5, 100),
    )

    result = top_daily_gap(rows, date(2026, 9, 10))

    assert result is not None
    assert result.symbol == "BBB"
    assert result.direction == "DOWN"
    assert result.gap == pytest.approx(-6.0)
    assert result.gap_percent == pytest.approx(-3.0)
    assert result.weighted_gap == pytest.approx(1200.0)
    assert result.previous_close == 200.0
    assert result.open_price == 194.0
    assert result.high == 201.0
    assert result.low == 190.0
    assert result.close == 198.0
    assert result.lot_size == 200


def test_top_daily_gap_returns_none_for_missing_date() -> None:
    row = DailyGapObservation(date(2026, 9, 10), "AAA", 100.0, 102.0, 103.0, 99.0, 101.0, 50)
    assert top_daily_gap((row,), date(2026, 9, 11)) is None


def test_build_daily_gap_observations_accepts_iso_dates() -> None:
    observations = build_daily_gap_observations(
        [
            {
                "trading_date": "2026-09-10",
                "symbol": "AAA",
                "previous_close": 100,
                "open": 103,
                "high": 105,
                "low": 99,
                "close": 104,
                "lot_size": 75,
            }
        ]
    )

    assert observations[0].trading_date == date(2026, 9, 10)
    assert observations[0].gap == pytest.approx(3.0)
    assert observations[0].gap_percent == pytest.approx(3.0)
    assert observations[0].weighted_gap == pytest.approx(225.0)
