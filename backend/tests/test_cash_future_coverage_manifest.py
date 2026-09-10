from datetime import datetime, timezone

import pytest

from app.backtesting.cash_future_coverage_manifest import (
    CoverageRange,
    build_coverage_manifest,
    expected_timestamp_count,
    manifest_from_catalog,
    manifest_summary,
)


def test_expected_timestamp_count_is_inclusive():
    assert expected_timestamp_count(0, 4, 2) == 3


def test_manifest_is_sorted_and_aggregated():
    manifest = build_coverage_manifest(
        source="test",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ranges=(
            CoverageRange("FUT", 20, 30, 2, 2, 0, True),
            CoverageRange("SPOT", 0, 10, 3, 2, 1, False),
        ),
    )
    assert [item.instrument for item in manifest.ranges] == ["FUT", "SPOT"]
    assert manifest.expected_points == 5
    assert manifest.observed_points == 4
    assert manifest.missing_points == 1
    assert not manifest.complete
    assert manifest_summary(manifest)["missing_points"] == 1


def test_manifest_from_catalog_counts_unique_observed_points():
    manifest = manifest_from_catalog(
        source="angelone",
        instrument="NIFTY-FUT",
        start_ns=0,
        end_ns=4,
        interval_ns=2,
        observed_timestamps=(0, 0, 4, 100),
    )
    item = manifest.ranges[0]
    assert item.expected_points == 3
    assert item.observed_points == 2
    assert item.missing_points == 1
    assert not item.complete


def test_invalid_coverage_counts_fail_closed():
    with pytest.raises(ValueError, match="observed_points"):
        build_coverage_manifest(
            source="test",
            ranges=(CoverageRange("X", 0, 1, 2, 0, 0, True),),
        )

    with pytest.raises(ValueError, match="interval_ns"):
        expected_timestamp_count(0, 1, 0)
