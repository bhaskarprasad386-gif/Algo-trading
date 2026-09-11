from datetime import date, datetime, timedelta

import pytest

from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, gap, month="SEP", expiry=date(2026, 9, 30), margin=10000.0, **quotes):
    return CashFutureHistoryPoint(timestamp=ts, symbol="ABC", contract_month=month,
        cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
        lot_size=100, margin_required=margin, expiry_date=expiry, **quotes)


def test_strategy_applies_only_selected_date_range_and_normalizes_buy_sell():
    start = datetime(2026, 9, 2, 10, 0)
    seen = []
    def strategy(current, history):
        seen.append((current.timestamp, tuple(p.timestamp for p in history)))
        return "BUY" if current.gap >= 10 else "SELL" if current.gap <= 4 else "HOLD"
    result = run_cash_future_strategy([point(start - timedelta(days=1), 12), point(start, 10),
        point(start + timedelta(hours=1), 4), point(start + timedelta(days=1), 3)], strategy,
        strategy_id="gap-test", config=CashFutureStrategyConfig(start_date=start.date(), end_date=start.date()))
    assert len(seen) == 3
    assert seen[0][0] == start - timedelta(days=1)
    assert seen[0][1] == (start - timedelta(days=1),)
    assert seen[1][1] == (start - timedelta(days=1), start)
    assert seen[2][1] == (start - timedelta(days=1), start + timedelta(hours=1))
    assert len(result.signals) == 2
    assert result.trades[0]["entry_time"] == start.isoformat()
    assert result.trades[0]["exit_time"] == (start + timedelta(hours=1)).isoformat()
    assert result.net_profit == 600.0


def test_strategy_cannot_see_future_observations():
    now = datetime(2026, 9, 2, 10, 0)
    seen_lengths = []
    def strategy(current, history):
        seen_lengths.append(len(history)); return "HOLD"
    run_cash_future_strategy([point(now, 10), point(now + timedelta(minutes=1), 9), point(now + timedelta(minutes=2), 8)], strategy, strategy_id="lookahead-test")
    assert seen_lengths == [1, 2, 3]


def test_strategy_is_deterministic_and_contract_isolated():
    now = datetime(2026, 9, 2, 10, 0)
    points = [point(now, 10, "SEP"), point(now + timedelta(hours=1), 4, "SEP")]
    def strategy(current, history):
        return "BUY" if current.gap >= 10 else "SELL" if current.gap <= 4 else "NONE"
    first = run_cash_future_strategy(points, strategy, strategy_id="det")
    second = run_cash_future_strategy(points, strategy, strategy_id="det")
    assert first == second
    with pytest.raises(ValueError, match="multiple contract months"):
        run_cash_future_strategy([point(now, 10, "SEP"), point(now + timedelta(hours=1), 4, "OCT")], strategy, strategy_id="mixed")


def test_strategy_persists_metadata_signals_trades_and_equity():
    now = datetime(2026, 9, 2, 10, 0)
    ledger = BacktestLedger()
    def strategy(current, history): return "BUY" if current.gap >= 10 else "SELL"
    result = run_cash_future_strategy([point(now, 10), point(now + timedelta(hours=1), 4)], strategy,
        strategy_id="persisted-gap", strategy_version="2",
        config=CashFutureStrategyConfig(charges_per_trade=20, funding_cost_per_trade=10),
        ledger=ledger, run_id="cash-future-p0-1", strategy_hash="abc123")
    assert result.final_capital == 10000570.0
    assert result.final_available_capital == 10000570.0
    assert result.final_reserved_margin == 0.0
    metadata = ledger.run_metadata("cash-future-p0-1")
    assert metadata["strategy_id"] == "persisted-gap"
    assert metadata["strategy_version"] == "2"
    assert len(ledger.records("cash-future-p0-1", "signal")) == 2
    assert len(ledger.records("cash-future-p0-1", "trade")) == 1
    equity = ledger.records("cash-future-p0-1", "equity")
    assert len(equity) == 2
    assert equity[0].payload["reserved_margin"] == 10000.0
    assert equity[0].payload["available_capital"] == 99990000.0
    assert equity[-1].payload["equity"] == 10000570.0
    assert equity[-1].payload["available_capital"] == 10000570.0
    ledger.close()


def test_strategy_blocks_entry_when_margin_exceeds_available_capital():
    now = datetime(2026, 9, 2, 10, 0)
    result = run_cash_future_strategy(
        [point(now, 10, margin=10000.0)],
        lambda current, history: "BUY",
        strategy_id="margin-block",
        config=CashFutureStrategyConfig(initial_capital=5000.0),
    )
    assert result.trades == ()
    assert result.final_capital == 5000.0
    assert result.final_available_capital == 5000.0
    assert result.final_reserved_margin == 0.0
    assert result.blocked_entry_count == 1
    assert result.signals[0]["execution_status"] == "blocked"
    assert result.signals[0]["blocked_reason"] == "insufficient_available_capital"
    assert result.signals[0]["required_margin"] == 10000.0


def test_strategy_releases_reserved_margin_after_exit_and_updates_realized_capital():
    now = datetime(2026, 9, 2, 10, 0)
    result = run_cash_future_strategy(
        [point(now, 10, margin=7000.0), point(now + timedelta(hours=1), 4, margin=9000.0)],
        lambda current, history: "BUY" if current.timestamp == now else "SELL",
        strategy_id="margin-release",
        config=CashFutureStrategyConfig(initial_capital=10000.0),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["reserved_margin"] == 7000.0
    assert result.final_reserved_margin == 0.0
    assert result.final_available_capital == 10600.0
    assert result.final_capital == 10600.0
    assert result.equity_curve[0]["available_capital"] == 3000.0
    assert result.equity_curve[-1]["available_capital"] == 10600.0


def test_strategy_uses_executable_bid_ask_sides_and_costs():
    now = datetime(2026, 9, 2, 10, 0)
    entry = point(now, 10, cash_bid=99.0, cash_ask=101.0, future_bid=109.0, future_ask=111.0)
    exit_point = point(now + timedelta(hours=1), 4, cash_bid=102.0, cash_ask=103.0, future_bid=104.0, future_ask=105.0)
    def strategy(current, history): return "BUY" if current is entry else "SELL"
    result = run_cash_future_strategy([entry, exit_point], strategy, strategy_id="bid-ask",
        config=CashFutureStrategyConfig(execution_model="bid_ask", charges_per_trade=25.0, funding_cost_per_trade=15.0))
    assert result.trades[0]["gross_profit"] == -500.0
    assert result.trades[0]["net_profit"] == -540.0
    assert result.net_profit == -540.0


def test_strategy_rejects_missing_executable_price_without_fabricating_fill():
    now = datetime(2026, 9, 2, 10, 0)
    entry = point(now, 10, cash_ask=101.0, future_bid=109.0)
    exit_point = point(now + timedelta(hours=1), 4, cash_bid=102.0, future_ask=None)
    def strategy(current, history): return "BUY" if current is entry else "SELL"
    with pytest.raises(ValueError, match="bid_ask execution requires"):
        run_cash_future_strategy([entry, exit_point], strategy, strategy_id="strict-bid-ask",
            config=CashFutureStrategyConfig(execution_model="bid_ask"))


def test_strategy_closes_open_position_at_observed_historical_expiry():
    start = datetime(2026, 9, 29, 10, 0)
    expiry_point = datetime(2026, 9, 30, 15, 30)

    def strategy(current, history):
        return "BUY" if current.timestamp == start else "HOLD"

    result = run_cash_future_strategy(
        [point(start, 10), point(expiry_point, 4)],
        strategy,
        strategy_id="expiry-exit",
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "expiry"
    assert result.trades[0]["exit_time"] == expiry_point.isoformat()
    assert result.net_profit == 600.0
