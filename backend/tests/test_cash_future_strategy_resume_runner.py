from datetime import datetime, timedelta

from app.backtesting.cash_future_strategy_resume_runner import resume_cash_future_strategy
from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _points():
    base = datetime(2026, 9, 10, 9, 15)
    gaps = [5.0, 5.5, 2.0, 1.0]
    return [
        CashFutureHistoryPoint(
            timestamp=base + timedelta(minutes=i),
            symbol="SBIN",
            contract_month="SEP",
            cash_price=100.0,
            future_price=100.0 + gap,
            gap=gap,
            gap_pct=gap,
            lot_size=100,
            margin_required=10000.0,
        )
        for i, gap in enumerate(gaps)
    ]


def _strategy(point, history):
    if point.timestamp.minute == 16:
        return "BUY"
    if point.timestamp.minute == 17:
        return "SELL"
    return "NONE"


def test_resume_continues_after_checkpoint_without_duplicate_records():
    ledger = BacktestLedger(":memory:")
    config = CashFutureStrategyConfig(
        initial_capital=100000.0,
        checkpoint_interval=2,
        history_window=10,
    )
    points = _points()

    run_cash_future_strategy(
        points[:2],
        _strategy,
        strategy_id="resume-test",
        strategy_version="1",
        config=config,
        ledger=ledger,
        run_id="run-1",
        strategy_hash="hash-1",
        data_source_fingerprint="source-1",
    )

    resumed = resume_cash_future_strategy(
        points,
        _strategy,
        ledger=ledger,
        run_id="run-1",
        strategy_id="resume-test",
        strategy_version="1",
        config=config,
        strategy_hash="hash-1",
        data_source_fingerprint="source-1",
    )

    assert len(resumed.signals) == 3
    assert len(resumed.trades) == 1
    assert resumed.trades[0]["entry_time"] == points[1].timestamp.isoformat()
    assert resumed.trades[0]["exit_time"] == points[2].timestamp.isoformat()
    assert len(resumed.equity_curve) == 3


def test_resume_rejects_source_that_ends_at_checkpoint():
    ledger = BacktestLedger(":memory:")
    config = CashFutureStrategyConfig(
        initial_capital=100000.0,
        checkpoint_interval=2,
    )
    points = _points()
    run_cash_future_strategy(
        points[:2],
        _strategy,
        strategy_id="resume-test",
        strategy_version="1",
        config=config,
        ledger=ledger,
        run_id="run-1",
        strategy_hash="hash-1",
        data_source_fingerprint="source-1",
    )

    try:
        resume_cash_future_strategy(
            points[:2],
            _strategy,
            ledger=ledger,
            run_id="run-1",
            strategy_id="resume-test",
            strategy_version="1",
            config=config,
            strategy_hash="hash-1",
            data_source_fingerprint="source-1",
        )
    except ValueError as exc:
        assert "does not contain observations after checkpoint" in str(exc)
    else:
        raise AssertionError("resume should reject a source with no post-checkpoint observations")
