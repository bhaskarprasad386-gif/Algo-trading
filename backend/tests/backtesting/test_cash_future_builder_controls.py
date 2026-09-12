from datetime import date, datetime, timezone

from app.backtesting.cash_future_strategy_routes import StrategyRunRequest, _build_builder_strategy
from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(gap: float, *, minute: int = 15) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=datetime(2026, 1, 2, 9, minute, tzinfo=timezone.utc),
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
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    strategy = _build_builder_strategy(request)
    assert strategy(point(5.0), ()) == "BUY"
    assert strategy(point(3.0), (point(5.0),)) == "SELL"


def test_builder_reverse_cash_sell_future_buy_uses_opposite_gap_direction():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="SELL",
        future_side="BUY",
        target=2.0,
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
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
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    strategy = _build_builder_strategy(request)
    assert strategy(point(5.0), ()) == "BUY"
    assert strategy(point(7.5), (point(5.0),)) == "SELL"


def test_runner_default_direction_applies_gap_profit_and_capital():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="BUY",
        future_side="SELL",
        target=2.0,
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    strategy = _build_builder_strategy(request)
    result = run_cash_future_strategy(
        [point(5.0), point(3.0, minute=16)],
        strategy,
        strategy_id=request.strategy_id,
        config=CashFutureStrategyConfig(initial_capital=10_000.0),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["gross_profit"] == 20.0
    assert result.net_profit == 20.0
    assert result.final_capital == 10_020.0


def test_runner_reverse_direction_applies_opposite_gap_profit():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="SELL",
        future_side="BUY",
        target=2.0,
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    strategy = _build_builder_strategy(request)
    result = run_cash_future_strategy(
        [point(-5.0), point(-3.0, minute=16)],
        strategy,
        strategy_id=request.strategy_id,
        config=CashFutureStrategyConfig(initial_capital=10_000.0, cash_side="SELL", future_side="BUY"),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["gross_profit"] == 20.0
    assert result.net_profit == 20.0


def test_runner_slippage_is_deducted_for_both_legs():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        cash_side="BUY",
        future_side="SELL",
        target=2.0,
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    strategy = _build_builder_strategy(request)
    result = run_cash_future_strategy(
        [point(5.0), point(3.0, minute=16)],
        strategy,
        strategy_id=request.strategy_id,
        config=CashFutureStrategyConfig(initial_capital=10_000.0, slippage_per_share=0.5),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["gross_profit"] == 10.0
    assert result.trades[0]["slippage_per_share"] == 0.5
    assert result.net_profit == 10.0
