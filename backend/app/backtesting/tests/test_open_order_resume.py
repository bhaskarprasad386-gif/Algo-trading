import pytest

from app.backtesting.durable_replay import DurableEventBacktestEngine
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, OrderType, SimOrder, TimeInForce
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision


def _events():
    return [
        MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]}, sequence=1),
        MarketEvent(2_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 7]], "bids": [[99.0, 5]]}, sequence=2),
    ]


class PartialLimitStrategy:
    strategy_id = "resume-open"
    strategy_version = "1"

    def on_event(self, event, context):
        if event.sequence == 1:
            return StrategyDecision(
                action="BUY",
                orders=(SimOrder("open-limit", "NIFTY", ExecutionSide.BUY, 10,
                                 order_type=OrderType.LIMIT, limit_price=100.0,
                                 time_in_force=TimeInForce.GTC),),
            )
        return None


def test_resume_restores_open_order_and_finishes_same_as_uninterrupted(tmp_path):
    db = tmp_path / "open-order-resume.sqlite"
    ledger = BacktestLedger(str(db))
    ledger.start_run("resume-open-run", "resume-open", "1", 100_000)
    first = DurableEventBacktestEngine(
        EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(100_000)),
        ledger,
        "resume-open-run",
        checkpoint_interval=1,
    )

    class InterruptingStrategy(PartialLimitStrategy):
        def on_event(self, event, context):
            decision = super().on_event(event, context)
            if event.sequence == 2:
                raise RuntimeError("interrupt after checkpoint")
            return decision

    with pytest.raises(RuntimeError, match="interrupt after checkpoint"):
        first.run(_events(), InterruptingStrategy())

    checkpoint = ledger.load_checkpoint("resume-open-run")
    assert checkpoint is not None
    assert checkpoint.state["source_cursor"] == 1
    assert checkpoint.state["order_lifecycle_state"][0]["status"] == "PARTIALLY_FILLED"
    assert checkpoint.state["market_state"]["open_orders"][0]["order_id"] == "open-limit"
    ledger.close()

    ledger = BacktestLedger(str(db))
    resumed_portfolio = Portfolio(100_000)
    resumed = DurableEventBacktestEngine(
        EventBacktestEngine(execution=ExecutionSimulator(), portfolio=resumed_portfolio),
        ledger,
        "resume-open-run",
        checkpoint_interval=1,
    )
    resumed_result = resumed.run(_events(), PartialLimitStrategy(), resume=True)

    full_portfolio = Portfolio(100_000)
    full_ledger = BacktestLedger()
    full_ledger.start_run("full-open-run", "resume-open", "1", 100_000)
    full_result = DurableEventBacktestEngine(
        EventBacktestEngine(execution=ExecutionSimulator(), portfolio=full_portfolio),
        full_ledger,
        "full-open-run",
        checkpoint_interval=1,
    ).run(_events(), PartialLimitStrategy())

    assert resumed_result.fills == 1
    assert full_result.fills == 2
    assert resumed_portfolio.snapshot().cash == pytest.approx(full_portfolio.snapshot().cash)
    assert resumed_portfolio.snapshot().positions == full_portfolio.snapshot().positions
    assert resumed_portfolio.trades == full_portfolio.trades
    assert resumed.engine.open_orders == {}
    ledger.close()
    full_ledger.close()
