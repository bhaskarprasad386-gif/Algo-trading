from pathlib import Path

import pytest

from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.universal_factory import create_universal_ledger, create_universal_run


class DummySource:
    def iter_events(self, *, start_ns=None, end_ns=None):
        return iter(())


def strategy(_context):
    return None


def make_spec(capital=100_000.0):
    return BacktestRunSpec(
        run_id="factory-run",
        strategy_id="universal",
        strategy_version="v1",
        instrument="NSE:ABC",
        start_ns=1,
        end_ns=2,
        resolution=BacktestResolution("tick", "test", 1, 2),
        parameters={},
        data_watermarks={"NSE:ABC": 2},
        initial_capital=capital,
    )


def test_create_universal_ledger_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "results.db"
    ledger = create_universal_ledger(path)
    try:
        assert path.exists()
        assert ledger.connection is not None
    finally:
        ledger.close()


def test_create_universal_run_binds_one_ledger_writer_engine_and_context(tmp_path):
    resources = create_universal_run(
        make_spec(),
        data_source=DummySource(),
        strategy=strategy,
        ledger_path=tmp_path / "results.db",
    )
    try:
        assert resources.writer.ledger is resources.ledger
        assert resources.engine.result_writer is resources.writer
        assert resources.context.result_writer is resources.writer
        assert resources.context.execution is resources.engine.execution
        assert resources.context.portfolio is resources.engine.portfolio
        assert resources.context.clock is resources.engine.clock
    finally:
        resources.close()


def test_create_universal_run_requires_initial_capital():
    spec = BacktestRunSpec(
        run_id="factory-no-capital",
        strategy_id="universal",
        strategy_version="v1",
        instrument="NSE:ABC",
        start_ns=1,
        end_ns=2,
        resolution=BacktestResolution("tick", "test", 1, 2),
    )
    with pytest.raises(ValueError, match="initial_capital"):
        create_universal_run(spec, data_source=DummySource(), strategy=strategy)


def test_create_universal_run_closes_ledger_when_engine_creation_fails(tmp_path):
    path = tmp_path / "cleanup" / "results.db"
    spec = make_spec()
    with pytest.raises(ValueError, match="quantity"):
        create_universal_run(

def test_universal_worker_claims_and_completes_new_run(tmp_path):
    from app.backtesting.universal_factory import UniversalBacktestWorker

    resources = create_universal_run(
        make_spec(),
        data_source=DummySource(),
        strategy=strategy,
        ledger_path=tmp_path / "results.db",
    )
    resources.close()

    result = UniversalBacktestWorker().run(
        make_spec(),
        data_source=DummySource(),
        strategy=strategy,
        ledger_path=tmp_path / "results.db",
    )
    assert result.final_equity == pytest.approx(100_000.0)

    ledger = create_universal_ledger(tmp_path / "results.db")
    try:
        assert ledger.run("factory-run")["status"] == "COMPLETED"
    finally:
        ledger.close()


def test_universal_worker_records_failure_and_closes_resources(tmp_path):
    from app.backtesting.universal_factory import UniversalBacktestWorker

    class FailingSource:
        def iter_events(self, *, start_ns=None, end_ns=None):
            raise RuntimeError("source failure")
            yield

    with pytest.raises(RuntimeError, match="source failure"):
        UniversalBacktestWorker().run(
            make_spec(),
            data_source=FailingSource(),
            strategy=strategy,
            ledger_path=tmp_path / "failed" / "results.db",
        )

    ledger = create_universal_ledger(tmp_path / "failed" / "results.db")
    try:
        assert ledger.run("factory-run")["status"] == "FAILED"
    finally:
        ledger.close()
