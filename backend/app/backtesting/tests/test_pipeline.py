from app.algo.pipeline import AllowAllRisk, CollectExecution, FunctionStrategy, Signal, StrategyExecutionPipeline
from app.backtesting.events import EventType, MarketEvent


def test_pipeline_dispatches_signal_through_risk_and_execution():
    strategy = FunctionStrategy(
        "test",
        lambda event, state: Signal(
            strategy="test",
            instrument=event.instrument,
            side="BUY",
            timestamp_ns=event.timestamp_ns,
            quantity=2,
        ),
    )
    execution = CollectExecution()
    pipeline = StrategyExecutionPipeline(strategy, AllowAllRisk(), execution)

    result = pipeline.on_event(
        MarketEvent(1_000_000, "NIFTY", EventType.TRADE, {"price": 100.0})
    )

    assert result["status"] == "accepted"
    assert len(execution.submitted) == 1
    assert execution.submitted[0][0].quantity == 2


def test_pipeline_rejects_zero_quantity_before_execution():
    strategy = FunctionStrategy(
        "test",
        lambda event, state: Signal(
            strategy="test",
            instrument=event.instrument,
            side="SELL",
            timestamp_ns=event.timestamp_ns,
            quantity=0,
        ),
    )
    execution = CollectExecution()
    pipeline = StrategyExecutionPipeline(strategy, AllowAllRisk(), execution)

    result = pipeline.on_event(
        MarketEvent(2_000_000, "NIFTY", EventType.QUOTE, {"ltp": 100.0})
    )

    assert result["status"] == "rejected"
    assert len(execution.submitted) == 0
