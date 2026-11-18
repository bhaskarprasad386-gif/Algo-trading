from types import SimpleNamespace

import pytest

from app.backtesting.cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from app.backtesting.cash_future_universe_pipeline import CashFutureUniversePipelineResult


def _pipeline(store, *, start_ns=100, end_ns=200):
    queue = SimpleNamespace(
        spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"),
        all_requests=(
            SimpleNamespace(instrument="NSE:11:ABC-EQ", start_ns=start_ns, end_ns=end_ns),
            SimpleNamespace(instrument="NFO:101:ABC26OCT", start_ns=start_ns, end_ns=end_ns),
        ),
    )
    acquisition = SimpleNamespace(
        results=(SimpleNamespace(coverage=SimpleNamespace(complete=True), queue=queue),)
    )
    return CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows=2,
        materialized_underlyings=("ABC",),
        coverage_store=store,
    )


def _manifest(store, ranges):
    store.upsert(
        build_coverage_manifest(source="angelone", ranges=tuple(ranges)),
        timeframe="1m",
    )


def test_exact_request_coverage_rejects_complete_wrong_historical_range(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    _manifest(store, (
        CoverageRange("NSE:11:ABC-EQ", 1, 50, 50, 50, 0, True),
        CoverageRange("NFO:101:ABC26OCT", 1, 50, 50, 50, 0, True),
    ))
    pipeline = _pipeline(store)
    assert pipeline.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        pipeline.require_backtest_ready()


def test_exact_request_coverage_accepts_split_complete_ranges_without_gaps(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    _manifest(store, (
        CoverageRange("NSE:11:ABC-EQ", 100, 150, 51, 51, 0, True),
        CoverageRange("NSE:11:ABC-EQ", 150, 200, 51, 51, 0, True),
        CoverageRange("NFO:101:ABC26OCT", 100, 150, 51, 51, 0, True),
        CoverageRange("NFO:101:ABC26OCT", 150, 200, 51, 51, 0, True),
    ))
    assert _pipeline(store).backtest_ready is True


def test_exact_request_coverage_rejects_gap_between_complete_ranges(tmp_path):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    _manifest(store, (
        CoverageRange("NSE:11:ABC-EQ", 100, 150, 51, 51, 0, True),
        CoverageRange("NSE:11:ABC-EQ", 151, 200, 50, 50, 0, True),
        CoverageRange("NFO:101:ABC26OCT", 100, 200, 101, 101, 0, True),
    ))
    assert _pipeline(store).backtest_ready is False
