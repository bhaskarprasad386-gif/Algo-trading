from app.backtesting.cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore


def test_manifest_store_upsert_and_missing(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.sqlite")
    first = build_coverage_manifest(
        source="angelone",
        generated_at=1,
        ranges=(CoverageRange("FUT", 0, 10, 3, 2, 1, False),),
    )
    store.upsert(first)
    assert store.missing(source="angelone")[0].missing_points == 1

    second = build_coverage_manifest(
        source="angelone",
        generated_at=2,
        ranges=(CoverageRange("FUT", 0, 10, 3, 3, 0, True),),
    )
    store.upsert(second)
    assert store.missing(source="angelone") == ()
    assert store.ranges(source="angelone", instrument="FUT")[0].complete
