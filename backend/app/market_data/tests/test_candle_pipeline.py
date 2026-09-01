from datetime import datetime, timezone

from app.market_data.candle_pipeline import CandlePipeline


def test_pipeline_builds_candle_per_symbol():
    pipeline = CandlePipeline(interval_seconds=60)
    t0 = datetime(2026, 1, 1, 10, 0, 1, tzinfo=timezone.utc)

    assert pipeline.update({"symbol": "NIFTY", "ltp": 100, "volume": 10, "timestamp": t0}) is None
    assert pipeline.update({"symbol": "NIFTY", "ltp": 105, "volume": 5, "timestamp": t0.replace(second=30)}) is None

    candle = pipeline.update({"symbol": "NIFTY", "ltp": 102, "volume": 7, "timestamp": t0.replace(minute=1, second=1)})
    assert candle is not None
    assert (candle.open, candle.high, candle.low, candle.close, candle.volume) == (100, 105, 100, 105, 15)


def test_pipeline_keeps_symbols_independent():
    pipeline = CandlePipeline(interval_seconds=60)
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    pipeline.update({"symbol": "AAA", "ltp": 10, "timestamp": t0})
    pipeline.update({"symbol": "BBB", "ltp": 20, "timestamp": t0})

    assert pipeline.current("AAA").close == 10
    assert pipeline.current("BBB").close == 20
