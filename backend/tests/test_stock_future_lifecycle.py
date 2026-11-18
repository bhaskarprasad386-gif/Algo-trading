from datetime import date, datetime, timezone

import pytest

from app.backtesting.stock_future_lifecycle import lifecycle_for_expiry


def test_lifecycle_has_exact_contract_identity_and_expiry():
    lifecycle = lifecycle_for_expiry(
        exchange="NFO",
        segment="FUTSTK",
        symbol="ABC26SEP",
        token="101",
        expiry=date(2026, 9, 24),
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 24, 23, 59, 59, tzinfo=timezone.utc),
    )

    assert lifecycle.instrument_key == "NFO:FUTSTK:ABC26SEP:101:2026-09-24"
    assert lifecycle.contains(datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert not lifecycle.contains(datetime(2026, 9, 25, tzinfo=timezone.utc))


def test_lifecycle_rejects_timestamp_outside_contract_window():
    lifecycle = lifecycle_for_expiry(
        exchange="NFO",
        segment="FUTSTK",
        symbol="ABC26SEP",
        token="101",
        expiry=date(2026, 9, 24),
        start=1_000_000_000,
        end=3_000_000_000,
    )

    assert lifecycle.require_timestamp(2_000_000_000) == 2_000_000_000
    with pytest.raises(ValueError, match="outside lifecycle"):
        lifecycle.require_timestamp(4_000_000_000)
