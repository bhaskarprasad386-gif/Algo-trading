from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.ledger import BacktestLedger
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision, restore_strategy_state


def test_durable_replay_journals_events_decisions_and_checkpoint():
    ledger = BacktestLedger(); ledger.start_run("run-1", "journal", "1", 100_000)
    class Strategy:
        strategy_id = "journal"; strategy_version = "1"
        def on_event(self, event, context): return StrategyDecision(action="BUY", orders=(SimOrder("o1", "X", ExecutionSide.BUY, 1),))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(100_000))
    durable = DurableEventBacktestEngine(engine, ledger, "run-1")
    result = durable.run([MarketEvent(10, "X", EventType.QUOTE, {"bid": 99, "ask": 100})], Strategy())
    assert [r.record_type for r in ledger.records("run-1")] == ["RUN_START", "EVENT", "DECISION", "RUN_END"]
    assert result.fills == 1 and durable.resume_cursor() == 1
    checkpoint = ledger.load_checkpoint("run-1")
    assert checkpoint is not None and checkpoint.timestamp_ns == 10
    assert checkpoint.state["fills"] == 1 and checkpoint.state["strategy_state"] == {}
    assert checkpoint.state["source_cursor"] == 1 and checkpoint.state["portfolio_state"]["cash"] < 100_000
    ledger.close()


def test_durable_replay_persists_and_restores_strategy_state():
    ledger = BacktestLedger(); ledger.start_run("run-state", "stateful", "1", 1000)
    class StatefulStrategy:
        strategy_id = "stateful"; strategy_version = "1"
        def __init__(self): self.count = 0
        def on_event(self, event, context): self.count += 1
        def get_state(self): return {"count": self.count}
        def set_state(self, state): self.count = int(state["count"])
    strategy = StatefulStrategy()
    durable = DurableEventBacktestEngine(EventBacktestEngine(), ledger, "run-state")
    durable.run([MarketEvent(1, "X", EventType.TRADE, {"price": 10}), MarketEvent(2, "X", EventType.TRADE, {"price": 11})], strategy)
    state = durable.checkpoint_state(); assert state is not None and state["strategy_state"] == {"count": 2}
    restored = StatefulStrategy(); restore_strategy_state(restored, state["strategy_state"]); assert restored.count == 2
    ledger.close()


def test_durable_replay_checkpoint_survives_ledger_reopen(tmp_path):
    path = tmp_path / "durable.sqlite"; ledger = BacktestLedger(str(path)); ledger.start_run("run-2", "s", "1", 1000)
    DurableEventBacktestEngine(EventBacktestEngine(), ledger, "run-2", checkpoint_interval=2).run(
        [MarketEvent(1, "X", EventType.TRADE, {"price": 10}), MarketEvent(2, "X", EventType.TRADE, {"price": 11})], lambda event, state: None)
    ledger.close(); reopened = BacktestLedger(str(path))
    assert DurableEventBacktestEngine(EventBacktestEngine(), reopened, "run-2").resume_cursor() == 2
    assert len(reopened.records("run-2", "EVENT")) == 2; reopened.close()


def test_true_resume_restores_portfolio_strategy_market_state_and_cursor(tmp_path):
    events = [MarketEvent(1, "X", EventType.QUOTE, {"bid": 9, "ask": 10}), MarketEvent(2, "X", EventType.QUOTE, {"bid": 10, "ask": 11}),
              MarketEvent(3, "X", EventType.QUOTE, {"bid": 11, "ask": 12}), MarketEvent(4, "X", EventType.QUOTE, {"bid": 12, "ask": 13})]
    class Strategy:
        strategy_id = "resume"; strategy_version = "1"
        def __init__(self, fail_after=None): self.count, self.fail_after = 0, fail_after
        def on_event(self, event, context):
            self.count += 1
            if self.fail_after is not None and self.count > self.fail_after: raise RuntimeError("simulated interruption")
            return StrategyDecision(action="BUY", orders=(SimOrder(f"o{event.timestamp_ns}", "X", ExecutionSide.BUY, 1),))
        def get_state(self): return {"count": self.count}
        def set_state(self, state): self.count = int(state["count"])
    db = tmp_path / "resume.sqlite"; ledger = BacktestLedger(str(db)); ledger.start_run("resume-run", "resume", "1", 1000)
    first = DurableEventBacktestEngine(EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(1000)), ledger, "resume-run", checkpoint_interval=2)
    try: first.run(events, Strategy(fail_after=2))
    except RuntimeError as exc: assert str(exc) == "simulated interruption"
    checkpoint = ledger.load_checkpoint("resume-run"); assert checkpoint is not None and checkpoint.state["source_cursor"] == 2
    ledger.close()
    ledger = BacktestLedger(str(db)); resumed_portfolio = Portfolio(1000)
    resumed = DurableEventBacktestEngine(EventBacktestEngine(execution=ExecutionSimulator(), portfolio=resumed_portfolio), ledger, "resume-run", checkpoint_interval=2)
    resumed_result = resumed.run(events, Strategy(), resume=True)
    assert resumed_result.fills == 2 and resumed.resume_cursor() == 4
    assert len(ledger.records("resume-run", "RUN_RESUME")) == 1 and len(ledger.records("resume-run", "EVENT")) == 4
    full_portfolio = Portfolio(1000); full_ledger = BacktestLedger(); full_ledger.start_run("full-run", "resume", "1", 1000)
    DurableEventBacktestEngine(EventBacktestEngine(execution=ExecutionSimulator(), portfolio=full_portfolio), full_ledger, "full-run").run(events, Strategy())
    assert resumed_portfolio.snapshot().cash == full_portfolio.snapshot().cash
    assert resumed_portfolio.snapshot().equity == full_portfolio.snapshot().equity
    assert resumed_portfolio.trades == full_portfolio.trades
    ledger.close(); full_ledger.close()


def test_resume_rejects_schema_version_mismatch(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "schema.sqlite"))
    ledger.start_run("schema-run", "s", "1", 1000, schema_version=2, data_source_fingerprint="source-a")
    durable = DurableEventBacktestEngine(EventBacktestEngine(), ledger, "schema-run")
    try:
        durable.run([], lambda event, state: None, resume=True, schema_version=3, data_source_fingerprint="source-a")
    except ValueError as exc:
        assert "schema_version mismatch" in str(exc)
    else:
        raise AssertionError("schema mismatch must block resume")
    ledger.close()


def test_resume_rejects_data_source_fingerprint_mismatch(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "fingerprint.sqlite"))
    ledger.start_run("fingerprint-run", "s", "1", 1000, schema_version=2, data_source_fingerprint="source-a")
    durable = DurableEventBacktestEngine(EventBacktestEngine(), ledger, "fingerprint-run")
    try:
        durable.run([], lambda event, state: None, resume=True, schema_version=2, data_source_fingerprint="source-b")
    except ValueError as exc:
        assert "data_source_fingerprint mismatch" in str(exc)
    else:
        raise AssertionError("data source mismatch must block resume")
    ledger.close()
