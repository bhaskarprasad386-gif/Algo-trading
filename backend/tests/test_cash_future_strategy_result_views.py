from datetime import datetime, timedelta

from app.backtesting.cash_future_strategy_runner import run_cash_future_strategy
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, gap):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="ABC",
        contract_month="SEP",
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=100,
        margin_required=10000.0,
        expiry_date=None,
    )


def test_durable_result_views_read_incrementally_from_ledger():
    now = datetime(2026, 9, 2, 10, 0)
    ledger = BacktestLedger()
    result = run_cash_future_strategy(
        [point(now, 10), point(now + timedelta(minutes=1), 4)],
        lambda current, history: "BUY" if current.timestamp == now else "SELL",
        strategy_id="lazy-result",
        ledger=ledger,
        run_id="lazy-result-1",
    )

    assert len(result.signals) == 2
    assert result.signals[0]["action"] == "BUY"
    assert result.signals[-1]["action"] == "SELL"
    assert len(result.trades) == 1
    assert result.trades[0]["net_profit"] == 600.0
    assert len(result.equity_curve) == 2
    assert result.equity_curve[-1]["equity"] == 100000600.0
    assert ledger.record_count("lazy-result-1", "signal") == 2
    assert ledger.record_count("lazy-result-1", "trade") == 1
    assert ledger.record_count("lazy-result-1", "equity") == 2
    ledger.close()
