import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_expected_events import (
    build_expected_event_repair_plan,
    missing_expected_timestamps,
)
from app.backtesting.historical_sync import build_expected_event_plan


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


def test_explicit_event_repair_plan_is_bounded_without_fixed_cadence():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((_record(1_000),))
        plan = build_expected_event_repair_plan(
            catalog,
            source="tick-provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(1_000, 1_050, 1_100, 1_250, 2_000),
            max_request_ns=100,
        )
        assert [(item.start_ns, item.end_ns) for item in plan] == [
            (1_050, 1_100),
            (1_250, 1_250),
            (2_000, 2_000),
        ]
    finally:
        catalog.close()


def test_historical_sync_wraps_explicit_event_repair_plan():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((_record(1_000),))
        plan = build_expected_event_plan(
            catalog,
            source="tick-provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(1_000, 1_050, 1_100, 1_250, 2_000),
            max_request_ns=100,
        )
        assert [(item.start_ns, item.end_ns) for item in plan.requests] == [
            (1_050, 1_100),
            (1_250, 1_250),
            (2_000, 2_000),
        ]
    finally:
        catalog.close()


def test_explicit_event_repair_plan_rejects_invalid_bound():
    catalog = HistoricalCatalog()
    try:
        with pytest.raises(ValueError, match="max_request_ns must be positive"):
            build_expected_event_repair_plan(
                catalog,
                source="tick-provider",
                instrument="NSE:1:TEST",
                timeframe="tick",
                expected_timestamps=(1_000,),
                max_request_ns=0,
            )
    finally:
        catalog.close()
