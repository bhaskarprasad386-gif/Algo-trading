from app.backtesting.cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore


def test_manifest_store_keeps_timeframes_isolated(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.sqlite")
    one_minute = build_coverage_manifest(
        source="angelone",
        generated_at=1,
        ranges=(CoverageRange("FUT", 0, 10, 3, 2, 1, False),),
    )
    five_minute = build_coverage_manifest(
        source="angelone",
        generated_at=2,
        ranges=(CoverageRange("FUT", 0, 10, 3, 3, 0, True),),
    )
    store.upsert(one_minute, timeframe="1m")
    store.upsert(five_minute, timeframe="5m")

    assert len(store.ranges(source="angelone", timeframe="1m", instrument="FUT")) == 1
    assert store.ranges(source="angelone", timeframe="1m", instrument="FUT")[0].missing_points == 1
    assert store.ranges(source="angelone", timeframe="5m", instrument="FUT")[0].complete
    assert len(store.missing(source="angelone", timeframe="1m")) == 1
    assert store.missing(source="angelone", timeframe="5m") == ()
