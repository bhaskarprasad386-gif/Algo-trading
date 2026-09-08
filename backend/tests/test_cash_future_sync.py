from datetime import datetime

import pytest

from app.market_data.cash_future_sync import sync_cash_future_history
from app.market_data.historical_backtest_sync import (
    HistoricalBacktestInstrument,
    HistoricalBacktestSyncResult,
)


def _result(instrument, start, end, *, completed=True, rows=5):
    return HistoricalBacktestSyncResult(
        instrument_key=instrument.key,
        requested_start=start,
        requested_end=end,
        ranges_requested=1,
        ranges_completed=1 if completed else 0,
        rows_written=rows,
    )


def test_cash_future_sync_runs_legs_independently_and_aggregates_status():
    cash = HistoricalBacktestInstrument("ABC-EQ", "100", "NSE", "NSE", "CASH")
    future = HistoricalBacktestInstrument(
        "ABC25JANFUT", "200", "NFO", "NFO", "FUTURE", contract_month="2025-01"
    )
    start = datetime(2025, 1, 1)
    end = datetime(2025, 2, 1)
    calls = []

    def fake_sync(db, *, instrument, start, end, client, interval):
        calls.append(instrument.key)
        return _result(instrument, start, end, rows=7 if instrument is cash else 11)

    result = sync_cash_future_history(
        None,
        cash=cash,
        future=future,
        start=start,
        end=end,
        sync_fn=fake_sync,
    )

    assert calls == [cash.key, future.key]
    assert result.completed
    assert result.rows_written == 18
    assert result.cash.instrument_key == cash.key
    assert result.future.instrument_key == future.key


def test_cash_future_sync_does_not_mark_pair_complete_when_future_is_incomplete():
    cash = HistoricalBacktestInstrument("ABC-EQ", "100", "NSE", "NSE", "CASH")
    future = HistoricalBacktestInstrument("ABC25JANFUT", "200", "NFO", "NFO", "FUTURE")
    start = datetime(2025, 1, 1)
    end = datetime(2025, 2, 1)

    def fake_sync(db, *, instrument, start, end, client, interval):
        return _result(instrument, start, end, completed=instrument is cash)

    result = sync_cash_future_history(
        None,
        cash=cash,
        future=future,
        start=start,
        end=end,
        sync_fn=fake_sync,
    )

    assert not result.completed
    assert result.cash.completed
    assert not result.future.completed


@pytest.mark.parametrize(
    "cash_type,future_type,cash_segment,future_segment,error",
    [
        ("FUTURE", "FUTURE", "NSE", "NFO", "cash instrument_type"),
        ("CASH", "CASH", "NSE", "NFO", "future instrument_type"),
        ("CASH", "FUTURE", "BSE", "NFO", "cash segment"),
        ("CASH", "FUTURE", "NSE", "NSE", "future segment"),
    ],
)
def test_cash_future_sync_validates_leg_identity(
    cash_type, future_type, cash_segment, future_segment, error
):
    cash = HistoricalBacktestInstrument("ABC", "100", cash_segment, cash_segment, cash_type)
    future = HistoricalBacktestInstrument("ABC-FUT", "200", future_segment, future_segment, future_type)

    with pytest.raises(ValueError, match=error):
        sync_cash_future_history(
            None,
            cash=cash,
            future=future,
            start=datetime(2025, 1, 1),
            end=datetime(2025, 2, 1),
        )


def test_cash_future_sync_rejects_empty_window():
    cash = HistoricalBacktestInstrument("ABC", "100", "NSE", "NSE", "CASH")
    future = HistoricalBacktestInstrument("ABC-FUT", "200", "NFO", "NFO", "FUTURE")

    with pytest.raises(ValueError, match="start must be before end"):
        sync_cash_future_history(
            None,
            cash=cash,
            future=future,
            start=datetime(2025, 2, 1),
            end=datetime(2025, 2, 1),
        )
