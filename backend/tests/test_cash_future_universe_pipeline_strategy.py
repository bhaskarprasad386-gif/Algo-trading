from datetime import date

import pytest

from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig
from app.backtesting.cash_future_universe_pipeline import CashFutureUniversePipelineResult
from app.backtesting.cash_future_historical_loader import CashFutureHistorySelection


class StubLoader:
    def __init__(self, points):
        self.points = points
        self.called = False

    def iter_points(self, selection):
        self.called = True
        yield from self.points


class StubAcquisition:
    results = ()


def test_pipeline_strategy_requires_durable_readiness_before_loading():
    pipeline = CashFutureUniversePipelineResult(StubAcquisition(), 0)
    loader = StubLoader(())
    selection = CashFutureHistorySelection(
        spot_instrument="NSE:1:ABC-EQ",
        exchange="NSE",
        underlying="ABC",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )
    with pytest.raises(LookupError, match="backtest blocked"):
        pipeline.run_strategy(loader, selection, lambda current, history: "HOLD", strategy_id="blocked")
    assert loader.called is False


def test_pipeline_strategy_streams_loader_into_existing_strategy_runner(monkeypatch):
    pipeline = CashFutureUniversePipelineResult(StubAcquisition(), 1, ("ABC",))
    loader = StubLoader([])
    selection = CashFutureHistorySelection(
        spot_instrument="NSE:1:ABC-EQ",
        exchange="NSE",
        underlying="ABC",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )

    captured = {}

    def fake_runner(points, strategy, **kwargs):
        captured["points"] = points
        captured["strategy"] = strategy
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr("app.backtesting.cash_future_universe_pipeline.run_cash_future_strategy", fake_runner)
    result = pipeline.run_strategy(
        loader,
        selection,
        lambda current, history: "HOLD",
        strategy_id="historical-gap",
        strategy_version="2",
        config=CashFutureStrategyConfig(),
        run_id="run-1",
    )
    assert result == "ok"
    assert loader.called is True
    assert captured["points"] is not None
    assert iter(captured["points"]) is captured["points"]
    assert captured["kwargs"]["strategy_id"] == "historical-gap"
    assert captured["kwargs"]["strategy_version"] == "2"
    assert captured["kwargs"]["run_id"] == "run-1"
