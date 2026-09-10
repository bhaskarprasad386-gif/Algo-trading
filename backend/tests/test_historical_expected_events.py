import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_expected_events import missing_expected_timestamps


def _record(timestamp_ns: int) -> HistoricalRecord:
    return HistoricalRecord(
        source="tick-provider",
        instrument="NSE:1:TEST",
        timeframe="tick",
        timestamp_ns=timestamp_ns,
        payload={"price": 100},
    )


def test_explicit_expected_events_find_only_authoritative_missing_timestamps():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((_record(1_000), _record(5_000)))
        assert missing_expected_timestamps(
            catalog,
            source="tick-provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(1_000, 2_500, 5_000, 9_000),
        ) == (2_500, 9_000)
    finally:
        catalog.close()


def test_explicit_expected_events_do_not_invent_cadence():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((_record(1_000), _record(1_001), _record(9_999)))
        assert missing_expected_timestamps(
            catalog,
            source="tick-provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(1_000, 9_999),
        ) == ()
    finally:
        catalog.close()


def test_explicit_expected_events_reject_negative_timestamps():
    catalog = HistoricalCatalog()
    try:
        with pytest.raises(ValueError, match="cannot be negative"):
            missing_expected_timestamps(
                catalog,
                source="tick-provider",
                instrument="NSE:1:TEST",
                timeframe="tick",
                expected_timestamps=(-1, 1_000),
            )
    finally:
        catalog.close()
