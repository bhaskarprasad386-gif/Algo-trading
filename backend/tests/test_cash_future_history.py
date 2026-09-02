from datetime import datetime, timedelta

from app.scanner.cash_future_history import (
    CashFutureHistoryPoint,
    build_graph_series,
    find_historical_gap_matches,
)


def point(ts, month, gap):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="ABC",
        contract_month=month,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=100,
        margin_required=10000.0,
        net_profit=gap * 100,
        roi_pct=1.0,
    )


def test_gap_history_keeps_contract_months_separate():
    now = datetime(2026, 9, 2, 10, 0)
    points = [
        point(now - timedelta(days=2), "CURRENT", 5.0),
        point(now - timedelta(days=1), "NEAR", 8.0),
        point(now, "CURRENT", 9.0),
    ]
    matches = find_historical_gap_matches(points, target_gap=8.0, contract_month="CURRENT")
    assert [m.gap for m in matches] == [9.0]


def test_gap_history_tolerance_and_difference():
    now = datetime(2026, 9, 2, 10, 0)
    matches = find_historical_gap_matches(
        [point(now, "CURRENT", 7.5), point(now + timedelta(minutes=1), "CURRENT", 6.0)],
        target_gap=8.0,
        tolerance=1.0,
        contract_month="CURRENT",
    )
    assert len(matches) == 1
    assert matches[0].difference_from_target == -0.5


def test_graph_series_is_time_ordered_and_separate_by_contract():
    now = datetime(2026, 9, 2, 10, 0)
    series = build_graph_series(
        [point(now, "CURRENT", 9.0), point(now - timedelta(hours=1), "CURRENT", 7.0), point(now, "NEAR", 12.0)],
        contract_month="CURRENT",
    )
    assert series["gap"] == [7.0, 9.0]
    assert len(series["timestamps"]) == 2
    assert series["future"] == [107.0, 109.0]
