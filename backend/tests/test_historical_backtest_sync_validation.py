from datetime import datetime

import pytest

from app.market_data import historical_backtest_sync as sync


class FakeClient:
    def __init__(self, data):
        self.data = data

    def get_candles(self, exchange, token, interval, from_date, to_date):
        return {"status": True, "data": self.data}


def _instrument():
    return sync.HistoricalBacktestInstrument(
        symbol="RELIANCE",
        token="2885",
        exchange="NSE",
        segment="NSE",
        instrument_type="EQ",
    )


def test_sync_rejects_invalid_ohlc_range_before_coverage(monkeypatch):
    client = FakeClient([["2026-01-02T09:15:00", 100, 99, 98, 101, 5000]])
    monkeypatch.setattr(
        sync, "missing_ranges", lambda db, key, start, end: [(start, end)]
    )
    monkeypatch.setattr(
        sync, "record_coverage", lambda *args, **kwargs: pytest.fail("coverage must not be recorded")
    )

    with pytest.raises(ValueError, match="OHLC range"):
        sync.sync_historical_backtest_data(
            object(),
            instrument=_instrument(),
            start=datetime(2026, 1, 2, 9, 15),
            end=datetime(2026, 1, 2, 9, 16),
            client=client,
        )


def test_sync_rejects_provider_rows_outside_requested_range(monkeypatch):
    client = FakeClient([["2026-01-02T09:30:00", 100, 102, 99, 101, 5000]])
    monkeypatch.setattr(
        sync, "missing_ranges", lambda db, key, start, end: [(start, end)]
    )
    monkeypatch.setattr(
        sync, "upsert_1m_bars", lambda *args, **kwargs: pytest.fail("out-of-range rows must not be persisted")
    )
    monkeypatch.setattr(
        sync, "record_coverage", lambda *args, **kwargs: pytest.fail("coverage must not be recorded")
    )

    with pytest.raises(ValueError, match="no valid candles"):
        sync.sync_historical_backtest_data(
            object(),
            instrument=_instrument(),
            start=datetime(2026, 1, 2, 9, 15),
            end=datetime(2026, 1, 2, 9, 16),
            client=client,
        )
