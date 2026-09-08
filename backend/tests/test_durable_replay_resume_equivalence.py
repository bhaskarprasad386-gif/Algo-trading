from __future__ import annotations

import pytest

from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision


EVENTS = tuple(
    MarketEvent(timestamp_ns=i * 1_000, instrument="NSE:SBIN", event_type=EventType.TRADE,
                payload={"price": price}, sequence=i, source="test")
    for i, price in enumerate((100.0, 101.0, 102.0, 110.0, 111.0), start=1)
)


class DeterministicStrategy:
    strategy_id = "resume-equivalence"
    strategy_version = "1"

    def __init__(self, *, fail_on_count: int | None = None) -> None:
        self.count = 0
        self.fail_on_count = fail_on_count

    def get_state(self):
        return {"count": self.count}

    def set_state(self, state):
        self.count = int(state["count"])

    def on_event(self, event, context):
        self.count += 1
        if self.fail_on_count == self.count:
            raise RuntimeError("intentional interruption")
        if self.count == 1:
            return StrategyDecision(
                action="BUY",
                orders=(SimOrder("entry", event.instrument, ExecutionSide.BUY, 10),),
            )
        if self.count == 4:
            return StrategyDecision(
                action="SELL",
                orders=(SimOrder("exit", event.instrument, ExecutionSide.SELL, 10),),
            )
        return None


def _make_engine():
    return EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(10_000_000.0))


def _semantic_result(engine: EventBacktestEngine):
    trades = tuple(
        (t.order_id, t.instrument, t.side.value, t.quantity, t.price, t.fee,
         t.realized_pnl_delta, t.cash_after, t.equity_after, t.timestamp_ns)
        for t in engine.portfolio.trades
    )
    lifecycles = tuple(
        (order_id, lifecycle.export_state())
        for order_id, lifecycle in sorted(engine._order_lifecycles.items())
    )
    snapshot = engine.portfolio.snapshot()
    return trades, lifecycles, snapshot


def test_interrupted_resume_matches_fresh_orders_fills_and_portfolio():
    fresh_ledger = BacktestLedger(":memory:")
    fresh_engine = _make_engine()
    fresh = DurableEventBacktestEngine(fresh_engine, fresh_ledger, "fresh", checkpoint_interval=1)
    fresh_strategy = DeterministicStrategy()
    fresh.start_run(fresh_strategy, 10_000_000.0, data_source_fingerprint="events-v1")
    fresh.run(EVENTS, fresh_strategy, data_source_fingerprint="events-v1")

    resumed_ledger = BacktestLedger(":memory:")
    interrupted_engine = _make_engine()
    interrupted = DurableEventBacktestEngine(interrupted_engine, resumed_ledger, "resume", checkpoint_interval=1)
    interrupted_strategy = DeterministicStrategy(fail_on_count=4)
    interrupted.start_run(interrupted_strategy, 10_000_000.0, data_source_fingerprint="events-v1")
    with pytest.raises(RuntimeError, match="intentional interruption"):
        interrupted.run(EVENTS, interrupted_strategy, data_source_fingerprint="events-v1")

    checkpoint = resumed_ledger.load_checkpoint("resume")
    assert checkpoint is not None
    assert checkpoint.state["source_cursor"] == 3
    assert checkpoint.state["strategy_state"] == {"count": 3}
    assert len(interrupted_engine.portfolio.trades) == 1

    recovery_engine = _make_engine()
    recovery = DurableEventBacktestEngine(recovery_engine, resumed_ledger, "resume", checkpoint_interval=1)
    recovery_strategy = DeterministicStrategy()
    result = recovery.run(
        EVENTS,
        recovery_strategy,
        resume=True,
        data_source_fingerprint="events-v1",
    )

    assert result.events_dispatched == 2
    assert recovery.resume_cursor() == len(EVENTS)
    assert recovery_strategy.count == len(EVENTS)
    assert _semantic_result(recovery_engine) == _semantic_result(fresh_engine)

    # Resume must not duplicate the already-journaled source events.
    event_records = resumed_ledger.records("resume", "EVENT")
    assert len(event_records) == len(EVENTS)
    assert [r.payload["sequence"] for r in event_records] == [1, 2, 3, 4, 5]
