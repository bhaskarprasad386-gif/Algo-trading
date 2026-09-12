from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting.cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from app.backtesting.cash_future_data_quality import CashFutureDataQualityReport
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.cash_future_universe_pipeline import (
    CashFutureUniversePipelineResult,
    acquire_and_materialize_cash_future_universe,
)
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_backtest import BacktestConfig


def test_acquisition_result_queue_is_materialized_without_rebuilding_instruments(monkeypatch):
    start_ns = int(datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    cash = HistoricalFetchRequest("angelone", "NSE:11:ABC-EQ", "1m", start_ns, start_ns)
    future = HistoricalFetchRequest("angelone", "NFO:101:ABC26OCT", "1m", start_ns, start_ns)
    queue = CashFutureDownloadQueue(cash, (future,))
    acquisition = SimpleNamespace(results=(SimpleNamespace(queue=queue),))

    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.acquire_cash_future_universe",
        lambda **kwargs: acquisition,
    )

    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("angelone", cash.instrument, "1m", start_ns, {"close": 100.0}),
        HistoricalRecord("angelone", future.instrument, "1m", start_ns, {"close": 105.0}),
    ])
    universe = CashFutureFnoUniverse(
        stocks=(CashFutureUniverseItem("ABC", "2026-10", "101", "ABC26OCT", date(2026, 10, 29), 125),),
        indices=(),
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        result = acquire_and_materialize_cash_future_universe(
            service=object(),
            universe=universe,
            master_rows=(),
            start=datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
            db=db,
            catalog=catalog,
        )
        rows = db.scalars(select(CashFutureHistory)).all()

    assert result.materialized_rows == 1
    assert result.materialized_underlyings == ("ABC",)
    assert result.materialized_requests == (("NFO:101:ABC26OCT", start_ns, start_ns),)
    assert len(rows) == 1
    assert rows[0].symbol == "ABC"
    assert rows[0].contract_month == "2026-10"
    assert rows[0].cash_price == 100.0
    assert rows[0].future_price == 105.0
    catalog.close()


def test_pipeline_readiness_requires_every_acquisition_complete_and_materialized():
    complete = SimpleNamespace(complete=True)
    incomplete = SimpleNamespace(complete=False)
    queue = SimpleNamespace(spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"), all_requests=())

    ready = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=complete, queue=queue),)),
        materialized_rows=2,
        materialized_underlyings=("ABC",),
    )
    blocked_incomplete = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=incomplete, queue=queue),)),
        materialized_rows=2,
        materialized_underlyings=("ABC",),
    )
    blocked_empty = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=complete, queue=queue),)),
        materialized_rows=0,
        materialized_underlyings=(),
    )
    blocked_partial = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(
            SimpleNamespace(coverage=complete, queue=queue),
            SimpleNamespace(coverage=complete, queue=SimpleNamespace(spot=SimpleNamespace(instrument="NSE:22:XYZ-EQ"), all_requests=())),
        )),
        materialized_rows=2,
        materialized_underlyings=("ABC",),
    )

    assert ready.backtest_ready is True
    ready.require_backtest_ready()
    assert blocked_incomplete.backtest_ready is False
    assert blocked_empty.backtest_ready is False
    assert blocked_partial.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        blocked_incomplete.require_backtest_ready()
    with pytest.raises(LookupError, match="backtest blocked"):
        blocked_empty.require_backtest_ready()
    with pytest.raises(LookupError, match="backtest blocked"):
        blocked_partial.require_backtest_ready()


def test_pipeline_readiness_blocks_partial_materialization_within_one_underlying():
    start_ns = 1_000
    end_ns = 3_000
    queue = SimpleNamespace(
        spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"),
        futures=(
            SimpleNamespace(instrument="NFO:101:ABC26OCT", start_ns=start_ns, end_ns=end_ns),
            SimpleNamespace(instrument="NFO:102:ABC26NOV", start_ns=start_ns, end_ns=end_ns),
        ),
        all_requests=(),
    )
    acquisition = SimpleNamespace(results=(SimpleNamespace(coverage=SimpleNamespace(complete=True), queue=queue),))

    partial = CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows=10,
        materialized_underlyings=("ABC",),
        materialized_requests=(("NFO:101:ABC26OCT", start_ns, end_ns),),
    )

    assert partial.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        partial.require_backtest_ready()


def test_pipeline_readiness_does_not_treat_another_contract_as_materialized():
    start_ns = 1_000
    end_ns = 3_000
    queue = SimpleNamespace(
        spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"),
        futures=(SimpleNamespace(instrument="NFO:102:ABC26NOV", start_ns=start_ns, end_ns=end_ns),),
        all_requests=(),
    )
    acquisition = SimpleNamespace(results=(SimpleNamespace(coverage=SimpleNamespace(complete=True), queue=queue),))

    wrong_contract = CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows=10,
        materialized_underlyings=("ABC",),
        materialized_requests=(("NFO:101:ABC26OCT", start_ns, end_ns),),
    )

    assert wrong_contract.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        wrong_contract.require_backtest_ready()


def test_pipeline_manifest_gate_blocks_missing_requested_instrument(tmp_path):
    queue = SimpleNamespace(
        spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"),
        all_requests=(
            SimpleNamespace(instrument="NSE:11:ABC-EQ"),
            SimpleNamespace(instrument="NFO:101:ABC26OCT"),
        ),
    )
    acquisition = SimpleNamespace(results=(SimpleNamespace(coverage=SimpleNamespace(complete=True), queue=queue),))
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    store.upsert(
        build_coverage_manifest(
            source="angelone",
            ranges=(CoverageRange("NSE:11:ABC-EQ", 1, 3, 3, 3, 0, True),),
        ),
        timeframe="1m",
    )
    pipeline = CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows=2,
        materialized_underlyings=("ABC",),
        coverage_store=store,
    )

    assert pipeline.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        pipeline.require_backtest_ready()


def test_pipeline_manifest_gate_accepts_all_requested_instruments_complete(tmp_path):
    queue = SimpleNamespace(
        spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"),
        all_requests=(
            SimpleNamespace(instrument="NSE:11:ABC-EQ"),
            SimpleNamespace(instrument="NFO:101:ABC26OCT"),
        ),
    )
    acquisition = SimpleNamespace(results=(SimpleNamespace(coverage=SimpleNamespace(complete=True), queue=queue),))
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    store.upsert(
        build_coverage_manifest(
            source="angelone",
            ranges=(
                CoverageRange("NSE:11:ABC-EQ", 1, 3, 3, 3, 0, True),
                CoverageRange("NFO:101:ABC26OCT", 1, 3, 3, 3, 0, True),
            ),
        ),
        timeframe="1m",
    )
    pipeline = CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows=2,
        materialized_underlyings=("ABC",),
        coverage_store=store,
    )

    assert pipeline.backtest_ready is True
    pipeline.require_backtest_ready()


def test_pipeline_run_backtest_passes_quality_report_to_persisted_runner(monkeypatch):
    complete = SimpleNamespace(complete=True)
    queue = SimpleNamespace(spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"), all_requests=())
    pipeline = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=complete, queue=queue),)),
        materialized_rows=2,
        materialized_underlyings=("ABC",),
    )
    coverage = object()
    quality = CashFutureDataQualityReport(records_checked=2)
    captured = {}

    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.build_persisted_cash_future_coverage",
        lambda *args, **kwargs: coverage,
    )
    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.audit_persisted_cash_future_data_quality",
        lambda *args, **kwargs: quality,
    )

    def fake_run(db, config, **kwargs):
        captured.update(kwargs)
        return {"trade_count": 1, "net_profit": 60.0}

    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.run_persisted_cash_future_backtest",
        fake_run,
    )

    result = pipeline.run_backtest(object(), BacktestConfig(min_entry_gap=8.0, exit_gap=5.0), symbol="ABC", page_size=25)

    assert result["trade_count"] == 1
    assert captured["symbol"] == "ABC"
    assert captured["page_size"] == 25
    assert captured["coverage_report"] is coverage
    assert captured["quality_report"] is quality


def test_pipeline_run_backtest_blocks_before_persisted_history_access(monkeypatch):
    incomplete = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=SimpleNamespace(complete=False), queue=SimpleNamespace(spot=SimpleNamespace(instrument="NSE:11:ABC-EQ"), all_requests=())),)),
        materialized_rows=2,
        materialized_underlyings=("ABC",),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("persisted backtest must not run")

    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.run_persisted_cash_future_backtest",
        fail_if_called,
    )

    with pytest.raises(LookupError, match="backtest blocked"):
        incomplete.run_backtest(object(), BacktestConfig())
