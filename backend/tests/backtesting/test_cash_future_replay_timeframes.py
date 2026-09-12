from datetime import datetime, timezone

from app.backtesting.cash_future_replay_routes import _available_replay_intervals
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _point(seconds: int) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=datetime.fromtimestamp(seconds, tz=timezone.utc),
        symbol="AAA",
        contract_month="2026-01",
        cash_price=100.0,
        future_price=112.0,
        gap=12.0,
        gap_pct=12.0,
        lot_size=100,
        margin_required=125000.0,
    )


def test_replay_intervals_do_not_claim_one_second_for_minute_data():
    points = [_point(0), _point(60), _point(120)]
    assert _available_replay_intervals(points) == ["1m", "5m", "15m", "30m"]


def test_replay_intervals_expose_one_second_only_for_one_second_data():
    points = [_point(0), _point(1), _point(2)]
    assert _available_replay_intervals(points) == ["1s", "1m", "5m", "15m", "30m"]


def test_replay_intervals_have_safe_default_for_single_observation():
    assert _available_replay_intervals([_point(0)]) == ["1m", "5m", "15m", "30m"]
