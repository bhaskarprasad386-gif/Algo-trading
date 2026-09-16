import pytest

from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import Portfolio


def test_resume_rejects_changed_event_before_checkpoint_cursor(tmp_path):
    events = [
        MarketEvent(1, "X", EventType.TRADE, {"price": 10}),
        MarketEvent(2, "X", EventType.TRADE, {"price": 11}),
        MarketEvent(3, "X", EventType.TRADE, {"price": 12}),
    ]

    class Strategy:
        strategy_id = "identity"
        strategy_version = "1"

        def __init__(self):
            self.count = 0

        def on_event(self, event, context):
            self.count += 1
            if self.count > 2:
                raise RuntimeError("stop after checkpoint")

    path = tmp_path / "identity.sqlite"
    ledger = BacktestLedger(str(path))
    ledger.start_run("identity-run", "identity", "1", 1000)
    first = DurableEventBacktestEngine(EventBacktestEngine(), ledger, "identity-run", checkpoint_interval=2)
    with pytest.raises(RuntimeError, match="stop after checkpoint"):
        first.run(events, Strategy())
    checkpoint = ledger.load_checkpoint("identity-run")
    assert checkpoint is not None
    assert checkpoint.state["source_cursor"] == 2
    assert checkpoint.state["source_event_identity"] == {
        "timestamp_ns": 2,
        "instrument": "X",
        "event_type": "TRADE",
        "sequence": None,
        "source": None,
    }
    ledger.close()

    changed = [
        events[0],
        MarketEvent(2, "X", EventType.TRADE, {"price": 999}),
        events[2],
    ]
    reopened = BacktestLedger(str(path))
    resumed = DurableEventBacktestEngine(
        EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(1000)),
        reopened,
        "identity-run",
    )
    with pytest.raises(ValueError, match="resume source mismatch at checkpoint cursor"):
        resumed.run(changed, Strategy(), resume=True)
    reopened.close()
