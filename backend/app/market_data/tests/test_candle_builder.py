from datetime import datetime, timezone

import pytest

from app.market_data.candle_builder import CandleBuilder


def test_candle_builder_aggregates_ticks():
    builder = CandleBuilder(interval_seconds=60)
    t0 = datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc)
    assert builder.update(100, 10, t0) is None
    assert builder.update(105, 5, t0.replace(second=30)) is None

    completed = builder.update(102, 7, t0.replace(minute=1, second=0))
    assert completed is not None
    assert completed.open == 100
    assert completed.high == 105
    assert completed.low == 100
    assert completed.close == 105
    assert completed.volume == 15


def test_candle_builder_rejects_old_tick():
    builder = CandleBuilder()
    t0 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)
    builder.update(100, timestamp=t0)
    with pytest.raises(ValueError, match="older than current candle"):
        builder.update(99, timestamp=t0.replace(minute=0))
