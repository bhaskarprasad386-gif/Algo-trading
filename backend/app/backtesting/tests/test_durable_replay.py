from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.ledger import BacktestLedger
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision


def test_durable_replay_journals_events_decisions_and_checkpoint():
    ledger = BacktestLedger()
    ledger.start_run("run-1", "journal", "1", 100_000)

    class Strategy:
        strategy_id = "journal"
        strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "X", ExecutionSide.BUY, 1),))

    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(100_000))
    durable = DurableEventBacktestEngine(engine, ledger, "run-1")
    result = durable.run([MarketEvent(10, "X", EventType.QUOTE, {"bid": 99, "ask": 100})], Strategy())

    types = [r.record_type for r in ledger.records("run-1")]
    assert types == ["RUN_START", "EVENT", "DECISION", "RUN_END"]
    assert result.fills == 1
    assert durable.resume_cursor() == 1
    checkpoint = ledger.load_checkpoint("run-1")
    assert checkpoint is not None
    assert checkpoint.timestamp_ns == 10
    assert checkpoint.state["fills"] == 1
    ledger.close()


def test_durable_replay_checkpoint_survives_ledger_reopen(tmp_path):
    path = tmp_path / "durable.sqlite"
    ledger = BacktestLedger(str(path))
    ledger.start_run("run-2", "s", "1", 1000)
    durable = DurableEventBacktestEngine(EventBacktestEngine(), ledger, "run-2", checkpoint_interval=2)
    durable.run([MarketEvent(1, "X", EventType.TRADE, {"price": 10}), MarketEvent(2, "X", EventType.TRADE, {"price": 11})], lambda event, state: None)
    ledger.close()

    reopened = BacktestLedger(str(path))
    assert DurableEventBacktestEngine(EventBacktestEngine(), reopened, "run-2").resume_cursor() == 2
    assert len(reopened.records("run-2", "EVENT")) == 2
    reopened.close()
