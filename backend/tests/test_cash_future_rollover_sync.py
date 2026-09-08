from datetime import datetime

from app.market_data.cash_future_rollover_sync import sync_cash_future_rollover_history
from app.market_data.historical_backtest_sync import HistoricalBacktestInstrument, HistoricalBacktestSyncResult


def _row(symbol, token, expiry):
    return {"symbol": symbol, "token": token, "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": expiry, "lotsize": "250"}


def _result(instrument, start, end):
    return HistoricalBacktestSyncResult(instrument_key=instrument.key, requested_start=start, requested_end=end, ranges_requested=1, ranges_completed=1, rows_written=10)


def test_rollover_sync_maps_each_future_window_to_distinct_contracts():
    cash = HistoricalBacktestInstrument("ABC-EQ", "100", "NSE", "NSE", "CASH")
    rows = [_row("ABCJAN", "1", "30JAN2025"), _row("ABCFEB", "2", "27FEB2025")]
    calls = []

    def fake_sync(db, *, instrument, start, end, client, interval, progress_fn=None):
        calls.append((instrument.token, start, end))
        if progress_fn:
            progress_fn(1, 1, 10)
        return _result(instrument, start, end)

    result = sync_cash_future_rollover_history(None, cash=cash, instrument_master_rows=rows, underlying="ABC", start=datetime(2025, 1, 1), end=datetime(2025, 3, 1), sync_fn=fake_sync)
    assert [c.token for c in result.contracts] == ["1", "2"]
    assert [token for token, _, _ in calls] == ["100", "1", "2"]
    assert result.completed
    assert result.rows_written == 30


def test_rollover_sync_reports_aggregate_leg_progress():
    cash = HistoricalBacktestInstrument("ABC-EQ", "100", "NSE", "NSE", "CASH")
    rows = [_row("ABCJAN", "1", "30JAN2025"), _row("ABCFEB", "2", "27FEB2025")]
    progress = []

    def fake_sync(db, *, instrument, start, end, client, interval, progress_fn=None):
        if progress_fn:
            progress_fn(1, 1, 10)
        return _result(instrument, start, end)

    result = sync_cash_future_rollover_history(
        None,
        cash=cash,
        instrument_master_rows=rows,
        underlying="ABC",
        start=datetime(2025, 1, 1),
        end=datetime(2025, 3, 1),
        sync_fn=fake_sync,
        progress_fn=lambda completed, total, rows_written: progress.append((completed, total, rows_written)),
    )

    assert result.completed
    assert progress[0] == (0, 3, 0)
    assert progress[-1] == (3, 3, 30)
    assert any(item == (1, 3, 10) for item in progress)
    assert any(item == (2, 3, 20) for item in progress)


def test_rollover_sync_requires_a_contract_covering_period():
    cash = HistoricalBacktestInstrument("ABC-EQ", "100", "NSE", "NSE", "CASH")
    try:
        sync_cash_future_rollover_history(None, cash=cash, instrument_master_rows=[_row("ABCJAN", "1", "30JAN2025")], underlying="ABC", start=datetime(2025, 2, 1), end=datetime(2025, 3, 1))
    except ValueError as exc:
        assert "no eligible NFO futures contracts" in str(exc)
    else:
        raise AssertionError("expected missing-contract validation")
