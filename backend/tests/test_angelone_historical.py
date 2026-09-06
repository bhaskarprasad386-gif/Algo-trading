from datetime import datetime, timezone

import pytest

from app.backtesting.angelone_historical import (
    AngelOneHistoricalSource,
    _timestamp_ns,
)
from app.backtesting.historical_ingest import HistoricalFetchRequest


class FakeClient:
    def getCandleData(self, params):
        assert params["exchange"] == "NSE"
        assert params["symboltoken"] == "3045"
        assert params["interval"] == "ONE_MINUTE"
        return {
            "status": True,
            "data": [["2026-09-01T09:15:00+05:30", 100, 102, 99, 101, 500, 1234]],
        }


class FakeAuth:
    def get_client(self):
        return FakeClient()


def test_timestamp_normalization():
    value = _timestamp_ns("2026-09-01T09:15:00+05:30")
    assert value == int(datetime(2026, 9, 1, 3, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_epoch_timestamp_units_normalize_to_same_ns():
    seconds = 1_756_701_900
    expected = seconds * 1_000_000_000
    assert _timestamp_ns(seconds) == expected
    assert _timestamp_ns(seconds * 1_000) == expected
    assert _timestamp_ns(seconds * 1_000_000) == expected
    assert _timestamp_ns(seconds * 1_000_000_000) == expected


def test_candle_rows_are_normalized_to_historical_records():
    source = AngelOneHistoricalSource(auth=FakeAuth())
    start = _timestamp_ns("2026-09-01T09:14:00+05:30")
    end = _timestamp_ns("2026-09-01T09:16:00+05:30")
    request = HistoricalFetchRequest("angelone", "NSE:3045:SBIN", "1m", start, end)

    records = tuple(source.fetch(request))
    assert len(records) == 1
    record = records[0]
    assert record.source == "angelone"
    assert record.instrument == "NSE:3045:SBIN"
    assert record.payload["open"] == 100.0
    assert record.payload["close"] == 101.0
    assert record.payload["volume"] == 500.0
    assert record.payload["open_interest"] == 1234.0


def test_unknown_timeframe_is_rejected():
    source = AngelOneHistoricalSource(auth=FakeAuth())
    request = HistoricalFetchRequest("angelone", "NSE:3045", "2m", 1, 2)
    with pytest.raises(ValueError, match="unsupported Angel One timeframe"):
        tuple(source.fetch(request))
