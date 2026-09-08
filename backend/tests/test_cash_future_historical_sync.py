from datetime import datetime

import pytest

from app.market_data.cash_future_sync import (
    CashFutureSyncResult,
    cash_future_missing_ranges,
    sync_cash_future_history,
)
from app.market_data.historical_backtest_sync import (
    HistoricalBacktestInstrument,
    HistoricalBacktestSyncResult,
    _chunk_range,
)


def test_one_minute_chunking_stays_within_provider_limit():
    start = datetime(2025, 1, 1, 9, 15)
    end = datetime(2025, 3, 15, 15, 30)
    chunks = _chunk_range(start, end)

    assert len(chunks) == 3
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    assert all((chunk_end - chunk_start).days <= 30 for chunk_start, chunk_end in chunks)
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))


def test_cash_future_sync_rejects_wrong_segments():
    cash = HistoricalBacktestInstrument("ABC", "1", "NSE", "NSE", "CASH")
    future = HistoricalBacktestInstrument("ABC-FUT", "2", "NSE", "NSE", "FUTURE")

    with pytest.raises(ValueError, match="future segment must be NFO"):
        sync_cash_future_history(
            None,
            cash=cash,
            future=future,
            start=datetime(2025, 1, 1),
            end=datetime(2025, 1, 2),
        )


def test_pair_result_requires_both_legs_complete():
    complete = HistoricalBacktestSyncResult("A", datetime(2025, 1, 1), datetime(2025, 1, 2), 1, 1, 10)
    incomplete = HistoricalBacktestSyncResult("B", datetime(2025, 1, 1), datetime(2025, 1, 2), 2, 1, 10)
    result = CashFutureSyncResult(complete, incomplete)

    assert result.completed is False
    assert result.rows_written == 20
