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


def test_manifest_store_repair_plan_is_scoped_and_idempotent(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.sqlite")
    manifest = build_coverage_manifest(
        source="angelone",
        generated_at=3,
        ranges=(
            CoverageRange("FUT-A", 100, 200, 10, 9, 1, False),
            CoverageRange("FUT-B", 300, 400, 10, 10, 0, True),
            CoverageRange("FUT-A", 500, 600, 10, 8, 2, False),
        ),
    )
    store.upsert(manifest)

    assert store.repair_plan(source="angelone") == (
        ("FUT-A", 100, 200),
        ("FUT-A", 500, 600),
    )
    assert store.repair_plan(source="angelone", instrument="FUT-B") == ()
    assert store.repair_plan(source="angelone", instrument="FUT-A") == (
        ("FUT-A", 100, 200),
        ("FUT-A", 500, 600),
    )

    store.upsert(
        build_coverage_manifest(
            source="angelone",
            generated_at=4,
            ranges=(CoverageRange("FUT-A", 100, 200, 10, 10, 0, True),),
        )
    )
    assert store.repair_plan(source="angelone", instrument="FUT-A") == (
        ("FUT-A", 500, 600),
    )
