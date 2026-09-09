from __future__ import annotations

import pytest

from app.backtesting.genuine_tick_historical_source import GenuineTickHistoricalSource
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest


def _request(timeframe: str = "s") -> HistoricalFetchRequest:
    return HistoricalFetchRequest(
        source="genuine_tick",
        instrument="NSE:3045:SBIN",
        timeframe=timeframe,
        start_ns=1_000_000_000,
        end_ns=1_000_000_003,
    )


def test_preserves_genuine_second_and_subsecond_timestamps() -> None:
    request = _request()
    rows = [
        HistoricalRecord("genuine_tick", request.instrument, request.timeframe, 1_000_000_001, {"price": 100.0}),
        HistoricalRecord("genuine_tick", request.instrument, request.timeframe, 1_000_000_002, {"price": 100.1}),
        HistoricalRecord("genuine_tick", request.instrument, request.timeframe, 1_000_000_003, {"price": 100.2}),
    ]
    source = GenuineTickHistoricalSource(lambda _: rows)

    result = tuple(source.fetch(request))

    assert [record.timestamp_ns for record in result] == [1_000_000_001, 1_000_000_002, 1_000_000_003]
    assert [record.payload["price"] for record in result] == [100.0, 100.1, 100.2]


def test_rejects_minute_timeframe_in_tick_source() -> None:
    source = GenuineTickHistoricalSource(lambda _: ())

    with pytest.raises(ValueError, match="genuine tick source"):
        tuple(source.fetch(_request("1m")))


def test_rejects_provider_identity_mismatch() -> None:
    request = _request()
    wrong = HistoricalRecord("other", request.instrument, request.timeframe, 1_000_000_001, {})
    source = GenuineTickHistoricalSource(lambda _: (wrong,))

    with pytest.raises(ValueError, match="wrong source"):
        tuple(source.fetch(request))


def test_raw_rows_require_timestamp_ns_and_do_not_resample() -> None:
    request = _request()
    source = GenuineTickHistoricalSource(lambda _: ())

    result = tuple(
        source.from_rows(
            request,
            [{"timestamp_ns": 1_000_000_001, "price": 100.0, "sequence": 7}],
        )
    )

    assert result[0].timestamp_ns == 1_000_000_001
    assert result[0].sequence == 7
    assert result[0].payload == {"price": 100.0}

    with pytest.raises(ValueError, match="timestamp_ns"):
        tuple(source.from_rows(request, [{"price": 100.0}]))
