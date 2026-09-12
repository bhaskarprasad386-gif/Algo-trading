from datetime import datetime, timedelta

import pytest

from app.backtesting.cash_future_strategy_resume_runner import resume_cash_future_strategy
from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.ledger import BacktestLedger, Checkpoint
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


class _StatefulStrategy:
    def __init__(self):
        self.count = 0

    def __call__(self, point, history):
        self.count += 1
        if self.count == 2:
            return "BUY"
        if self.count == 3:
            return "SELL"
        return "NONE"

    def checkpoint_state(self):
        return {"count": self.count}

    def restore_checkpoint_state(self, state):
        self.count = int(state["count"])


class _NonRestoringStrategy:
    def __init__(self):
        self.called = False

    def __call__(self, point, history):
        self.called = True
        return "NONE"


def _run_kwargs(config):
    return {
        "strategy_id": "resume-test",
        "strategy_version": "1",
        "config": config,
        "strategy_hash": "hash-1",
        "data_source_fingerprint": "source-1",
    }


def test_resume_matches_uninterrupted_run_without_duplicate_records():
    points = _points()
    config = CashFutureStrategyConfig(
        initial_capital=100000.0,
        checkpoint_interval=2,
        history_window=10,
    )

    full_ledger = BacktestLedger(":memory:")
    full = run_cash_future_strategy(
        points,
        _strategy,
        ledger=full_ledger,
        run_id="full-run",
        **_run_kwargs(config),
    )

    resumed_ledger = BacktestLedger(":memory:")
    run_cash_future_strategy(
        points[:2],
        _strategy,
        ledger=resumed_ledger,
        run_id="resumed-run",
        **_run_kwargs(config),
    )
    resumed = resume_cash_future_strategy(
        points,
        _strategy,
        ledger=resumed_ledger,
        run_id="resumed-run",
        **_run_kwargs(config),
    )

    assert list(resumed.signals) == list(full.signals)
    assert list(resumed.trades) == list(full.trades)
    assert list(resumed.equity_curve) == list(full.equity_curve)
    assert resumed.final_capital == full.final_capital
    assert resumed.net_profit == full.net_profit
    assert resumed.final_available_capital == full.final_available_capital
    assert resumed.final_reserved_margin == full.final_reserved_margin
    assert resumed.blocked_entry_count == full.blocked_entry_count


def test_stateful_strategy_checkpoint_restores_and_matches_uninterrupted_run():
    points = _points()
    config = CashFutureStrategyConfig(
        initial_capital=100000.0,
        checkpoint_interval=2,
        history_window=10,
    )

    full_ledger = BacktestLedger(":memory:")
    full = run_cash_future_strategy(
        points,
        _StatefulStrategy(),
        ledger=full_ledger,
        run_id="stateful-full",
        **_run_kwargs(config),
    )

    resumed_ledger = BacktestLedger(":memory:")
    run_cash_future_strategy(
        points[:2],
        _StatefulStrategy(),
        ledger=resumed_ledger,
        run_id="stateful-resumed",
        **_run_kwargs(config),
    )

    fresh_strategy = _StatefulStrategy()
    resumed = resume_cash_future_strategy(
        points,
        fresh_strategy,
        ledger=resumed_ledger,
        run_id="stateful-resumed",
        **_run_kwargs(config),
    )

    assert fresh_strategy.count == 4
    assert list(resumed.signals) == list(full.signals)
    assert list(resumed.trades) == list(full.trades)
    assert list(resumed.equity_curve) == list(full.equity_curve)
    assert resumed.final_capital == full.final_capital
    assert resumed.net_profit == full.net_profit


def test_stateful_resume_rejects_checkpoint_without_strategy_state():
    ledger = BacktestLedger(":memory:")
    config = CashFutureStrategyConfig(
        initial_capital=100000.0,
        checkpoint_interval=None,
    )
    points = _points()
    run_cash_future_strategy(
        points[:2],
        _strategy,
        ledger=ledger,
        run_id="missing-state",
        **_run_kwargs(config),
    )

    # No checkpoint exists when checkpointing is disabled, so the resume helper
    # must fail before a stateful strategy can continue.
    with pytest.raises(ValueError, match="checkpoint"):
        resume_cash_future_strategy(
            points,
            _StatefulStrategy(),
            ledger=ledger,
            run_id="missing-state",
            **_run_kwargs(config),
        )


def test_resume_rejects_stateful_checkpoint_for_non_restoring_strategy():
    points = _points()
    config = CashFutureStrategyConfig(initial_capital=100000.0, checkpoint_interval=2)
    ledger = BacktestLedger(":memory:")

    run_cash_future_strategy(
        points[:2],
        _StatefulStrategy(),
        ledger=ledger,
        run_id="state-contract",
        **_run_kwargs(config),
    )

    strategy = _NonRestoringStrategy()
    with pytest.raises(ValueError, match="cannot restore"):
        resume_cash_future_strategy(
            points,
            strategy,
            ledger=ledger,
            run_id="state-contract",
            **_run_kwargs(config),
        )
    assert strategy.called is False


def test_resume_rejects_malformed_open_entry_before_strategy_execution():
    points = _points()
    config = CashFutureStrategyConfig(initial_capital=100000.0, checkpoint_interval=2)
    ledger = BacktestLedger(":memory:")

    run_cash_future_strategy(
        points[:2],
        _strategy,
        ledger=ledger,
        run_id="bad-entry",
        **_run_kwargs(config),
    )
    row = ledger.load_checkpoint("bad-entry")
    assert row is not None
    state = dict(row.state)
    state["open_entry"] = {"symbol": "SBIN"}
    ledger.checkpoint(Checkpoint("bad-entry", row.event_index, row.timestamp_ns, state))

    with pytest.raises(ValueError, match="open_entry missing fields"):
        resume_cash_future_strategy(
            points,
            _strategy,
            ledger=ledger,
            run_id="bad-entry",
            **_run_kwargs(config),
        )


def test_resume_rejects_invalid_open_entry_values():
    points = _points()
    config = CashFutureStrategyConfig(initial_capital=100000.0, checkpoint_interval=2)
    ledger = BacktestLedger(":memory:")

    run_cash_future_strategy(
        points[:2],
        _strategy,
        ledger=ledger,
        run_id="bad-entry-value",
        **_run_kwargs(config),
    )
    row = ledger.load_checkpoint("bad-entry-value")
    assert row is not None
    state = dict(row.state)
    state["open_entry"] = {
        "timestamp": points[0].timestamp.isoformat(),
        "symbol": "SBIN",
        "contract_month": "SEP",
        "cash_price": "not-a-number",
        "future_price": 105.0,
        "gap": 5.0,
        "gap_pct": 5.0,
        "lot_size": 100,
        "margin_required": 10000.0,
        "expiry_date": None,
    }
    ledger.checkpoint(Checkpoint("bad-entry-value", row.event_index, row.timestamp_ns, state))

    with pytest.raises(ValueError, match="open_entry contains invalid values"):
        resume_cash_future_strategy(
            points,
            _strategy,
            ledger=ledger,
            run_id="bad-entry-value",
            **_run_kwargs(config),
        )


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
        ledger=ledger,
        run_id="run-1",
        **_run_kwargs(config),
    )

    resumed = resume_cash_future_strategy(
        points,
        _strategy,
        ledger=ledger,
        run_id="run-1",
        **_run_kwargs(config),
    )

    assert len(resumed.signals) == 4
    assert len(resumed.trades) == 1
    assert resumed.trades[0]["entry_time"] == points[1].timestamp.isoformat()
    assert resumed.trades[0]["exit_time"] == points[2].timestamp.isoformat()
    assert len(resumed.equity_curve) == 4


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
        ledger=ledger,
        run_id="run-1",
        **_run_kwargs(config),
    )

    try:
        resume_cash_future_strategy(
            points[:2],
            _strategy,
            ledger=ledger,
            run_id="run-1",
            **_run_kwargs(config),
        )
    except ValueError as exc:
        assert "does not contain observations after checkpoint" in str(exc)
    else:
        raise AssertionError("resume should reject a source with no post-checkpoint observations")
