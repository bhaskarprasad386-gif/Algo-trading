from datetime import date, datetime, timedelta

import pytest

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, gap, month="SEP", **quotes):
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
        expiry_date=date(2026, 9, 30),
        **quotes,
    )


def test_strategy_applies_only_selected_date_range_and_normalizes_buy_sell():
    start = datetime(2026, 9, 2, 10, 0)
    seen = []

    def strategy(current, history):
        seen.append((current.timestamp, tuple(p.timestamp for p in history)))
        return "BUY" if current.gap >= 10 else "SELL" if current.gap <= 4 else "HOLD"

    result = run_cash_future_strategy(
        [
            point(start - timedelta(days=1), 12),
            point(start, 10),
            point(start + timedelta(hours=1), 4),
            point(start + timedelta(days=1), 3),
        ],
        strategy,
        strategy_id="gap-test",
        config=CashFutureStrategyConfig(start_date=start.date(), end_date=start.date()),
    )

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
        seen_lengths.append(len(history))
        return "HOLD"

    run_cash_future_strategy(
        [point(now, 10), point(now + timedelta(minutes=1), 9), point(now + timedelta(minutes=2), 8)],
        strategy,
        strategy_id="lookahead-test",
    )
    assert seen_lengths == [1, 2, 3]


def test_strategy_is_deterministic_and_contract_isolated():
    now = datetime(2026, 9, 2, 10, 0)
    points = [point(now, 10, "SEP"), point(now + timedelta(hours=1), 4, "SEP")]

    def strategy(current, history):
        return "BUY" if current.gap >= 10 else "SELL" if current.gap <= 4 else "NONE"

    config = CashFutureStrategyConfig()
    first = run_cash_future_strategy(points, strategy, strategy_id="det", config=config)
    second = run_cash_future_strategy(points, strategy, strategy_id="det", config=config)
    assert first == second

    with pytest.raises(ValueError, match="multiple contract months"):
        run_cash_future_strategy(
            [point(now, 10, "SEP"), point(now + timedelta(hours=1), 4, "OCT")],
            strategy,
            strategy_id="mixed",
        )


def test_strategy_persists_metadata_signals_and_trades():
    now = datetime(2026, 9, 2, 10, 0)
    ledger = BacktestLedger()

    def strategy(current, history):
        return "BUY" if current.gap >= 10 else "SELL"

    result = run_cash_future_strategy(
        [point(now, 10), point(now + timedelta(hours=1), 4)],
        strategy,
        strategy_id="persisted-gap",
        strategy_version="2",
        config=CashFutureStrategyConfig(charges_per_trade=20, funding_cost_per_trade=10),
        ledger=ledger,
        run_id="cash-future-p0-1",
        strategy_hash="abc123",
    )

    assert result.final_capital == 10_000_570.0
    metadata = ledger.run_metadata("cash-future-p0-1")
    assert metadata["strategy_id"] == "persisted-gap"
    assert metadata["strategy_version"] == "2"
    assert len(ledger.records("cash-future-p0-1", "signal")) == 2
    assert len(ledger.records("cash-future-p0-1", "trade")) == 1
