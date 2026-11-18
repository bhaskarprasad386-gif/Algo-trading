from datetime import datetime

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(minute: int, gap: float) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=datetime(2026, 9, 11, 9, 15 + minute),
        symbol="SBIN",
        contract_month="SEP",
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=750,
        margin_required=10000.0,
    )


def test_runner_persists_periodic_and_final_checkpoint():
    ledger = BacktestLedger(":memory:")
    points = [point(0, 5.0), point(1, 4.0), point(2, 2.0)]

    run_cash_future_strategy(
        points,
        lambda current, history: "BUY" if len(history) == 1 else "NONE",
        strategy_id="checkpoint-test",
        strategy_version="1",
        config=CashFutureStrategyConfig(checkpoint_interval=2),
        ledger=ledger,
        run_id="run-checkpoint",
        strategy_hash="strategy-v1",
        data_source_fingerprint="source-v1",
    )

    latest = ledger.load_checkpoint("run-checkpoint")
    assert latest is not None
    assert latest.event_index == 3
    assert latest.timestamp_ns == int(points[-1].timestamp.timestamp() * 1_000_000_000)
    assert latest.state["strategy_id"] == "checkpoint-test"
    assert latest.state["selected_contract"] == "SEP"
    assert latest.state["reserved_margin"] == 10000.0
    assert latest.state["open_entry"]["symbol"] == "SBIN"

    history = ledger.checkpoint_history("run-checkpoint")
    assert [item.event_index for item in history] == [2, 3]


def test_checkpoint_disabled_does_not_create_checkpoint():
    ledger = BacktestLedger(":memory:")
    run_cash_future_strategy(
        [point(0, 5.0)],
        lambda current, history: "NONE",
        strategy_id="no-checkpoint",
        config=CashFutureStrategyConfig(),
        ledger=ledger,
        run_id="run-no-checkpoint",
    )
    assert ledger.load_checkpoint("run-no-checkpoint") is None
