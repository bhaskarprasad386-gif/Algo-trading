from datetime import date, datetime, timezone

from app.backtesting.cash_future_strategy_routes import StrategyRunRequest, _build_builder_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(gap: float) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=datetime(2026, 1, 2, 9, 15, tzinfo=timezone.utc),
        symbol="SBIN",
        contract_month="2026-01",
        cash_price=100.0,
        future_price=gap + 100.0,
        gap=gap,
        gap_pct=gap,
        lot_size=10,
        margin_required=1000.0,
        expiry_date=date(2026, 1, 29),
    )


def test_builder_default_cash_buy_future_sell_targets_gap_convergence():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="BUY",
        future_side="SELL",
        target=2.0,
    )
    strategy = _build_builder_strategy(request)
    history = ()
    assert strategy(point(5.0), history) == "BUY"
    assert strategy(point(3.0), (point(5.0),)) == "SELL"


def test_builder_reverse_cash_sell_future_buy_uses_opposite_gap_direction():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="SELL",
        future_side="BUY",
        target=2.0,
    )
    strategy = _build_builder_strategy(request)
    assert strategy(point(-5.0), ()) == "BUY"
    assert strategy(point(-3.0), (point(-5.0),)) == "SELL"


def test_builder_stop_loss_exits_when_spread_moves_against_selected_direction():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="BUY",
        future_side="SELL",
        stop_loss=2.0,
    )
    strategy = _build_builder_strategy(request)
    assert strategy(point(5.0), ()) == "BUY"
    assert strategy(point(7.5), (point(5.0),)) == "SELL"
