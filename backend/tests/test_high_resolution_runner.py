from app.backtesting.event_strategy import StrategyContext, StrategySignal
from app.backtesting.event_replay import ReplayEvent
from app.backtesting.high_resolution_runner import HighResolutionEventRunner
from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.ledger import BacktestLedger


class BuyOnPositiveTick:
    def on_event(self, context: StrategyContext):
        if context.data.get("buy"):
            return StrategySignal("BUY", 1, "tick")
        return None


class BuyThenSell:
    def on_event(self, context: StrategyContext):
        if context.data.get("side") == "BUY":
            return StrategySignal("BUY", 1, "tick")
        if context.data.get("side") == "SELL":
            return StrategySignal("SELL", 1, "tick")
        return None


def test_high_resolution_runner_orders_and_executes_events():
    events = [
        ReplayEvent(2_000_000, 1, {"price": 102.0, "buy": True}),
        ReplayEvent(1_000_000, 2, {"price": 100.0, "buy": True}),
    ]
    results = HighResolutionEventRunner().run(
        events, strategy=BuyOnPositiveTick(), instrument="NIFTY"
    )
    assert [r.fills[0].filled_at_ns for r in results] == [1_000_000, 2_000_000]
    assert [r.fills[0].price for r in results] == [100.0, 102.0]


def test_high_resolution_runner_persists_completed_trades_incrementally():
    ledger = BacktestLedger()
    ledger.start_run("hr", "tick", "1", 100_000)
    writer = HighResolutionLedgerWriter(ledger)
    events = [
        ReplayEvent(1_000_001, 1, {"price": 100.0, "side": "BUY"}),
        ReplayEvent(1_000_009, 2, {"price": 103.0, "side": "SELL"}),
    ]

    pnl = HighResolutionEventRunner().run_persisted(
        events,
        strategy=BuyThenSell(),
        instrument="NIFTY",
        run_id="hr",
        writer=writer,
    )

    assert pnl == 3.0
    records = ledger.records("hr", "high_resolution_trade")
    assert len(records) == 1
    assert records[0].payload["entry_timestamp_ns"] == 1_000_001
    assert records[0].payload["exit_timestamp_ns"] == 1_000_009
    assert records[0].payload["net_pnl"] == 3.0
