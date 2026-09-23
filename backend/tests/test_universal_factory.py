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
            spec,
            data_source=DummySource(),
            strategy=strategy,
            ledger_path=path,
            quantity=0,
        )
    assert path.exists()


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


def test_universal_worker_does_not_duplicate_engine_failure_record(tmp_path):
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
            ledger_path=tmp_path / "duplicate-failure" / "results.db",
        )

    ledger = create_universal_ledger(tmp_path / "duplicate-failure" / "results.db")
    try:
        assert ledger.run("factory-run")["status"] == "FAILED"
        rows = ledger.events("factory-run", limit=20)
        assert sum(row["event_type"] == "RUN_FAILED" for row in rows) == 1
    finally:
        ledger.close()

def test_universal_worker_reclaims_recoverable_checkpoint_and_resumes_without_duplicates(tmp_path):
    from app.backtesting.event_model import event_identity
    from app.backtesting.historical_catalog import HistoricalRecord
    from app.backtesting.universal_factory import UniversalBacktestWorker

    events = (
        HistoricalRecord("test", "AAA", "tick", 1, {"price": 100.0}, 1),
        HistoricalRecord("test", "AAA", "tick", 2, {"price": 110.0}, 2),
    )

    def make_source(interrupt=False):
        class Source:
            def iter_events(self, *, start_ns=None, end_ns=None):
                for index, event in enumerate(events):
                    yield event
                    if interrupt and index == 0:
                        raise SystemExit("simulated worker interruption")
        return Source()

    def strategy(context):
        return EventSignal("BUY" if context.timestamp_ns == 1 else "SELL")

    def spec(run_id):
        return BacktestRunSpec(
            run_id=run_id,
            strategy_id="universal",
            strategy_version="v1",
            instrument="AAA",
            start_ns=1,
            end_ns=2,
            resolution=BacktestResolution("tick", "historical", 1, 2),
            parameters={},
            data_watermarks={"AAA": 2},
            initial_capital=100_000.0,
        )

    reference = UniversalBacktestWorker().run(
        spec("worker-reference"),
        data_source=make_source(),
        strategy=strategy,
        ledger_path=tmp_path / "reference.db",
        checkpoint_every_events=1,
    )

    with pytest.raises(SystemExit, match="simulated worker interruption"):
        UniversalBacktestWorker().run(
            spec("worker-recovery"),
            data_source=make_source(interrupt=True),
            strategy=strategy,
            ledger_path=tmp_path / "recovery.db",
            checkpoint_every_events=1,
        )

    ledger = create_universal_ledger(tmp_path / "recovery.db")
    try:
        assert ledger.run("worker-recovery")["status"] == "RUNNING"
        checkpoint = ledger.checkpoints.load("worker-recovery")
        assert checkpoint is not None
        assert checkpoint.processed_events == 1
        assert checkpoint.state["source_event_identity"] == {
            "timestamp_ns": 1,
            "source": "test",
            "instrument": "AAA",
            "timeframe": "tick",
            "sequence": 1,
        }
        ledger.mark_recoverable("worker-recovery")
    finally:
        ledger.close()

    resumed = UniversalBacktestWorker().run(
        spec("worker-recovery"),
        data_source=make_source(),
        strategy=strategy,
        ledger_path=tmp_path / "recovery.db",
        resume=True,
        checkpoint_every_events=1,
    )

    assert resumed.final_equity == pytest.approx(reference.final_equity)
    assert resumed.realized_pnl == pytest.approx(reference.realized_pnl)
    assert resumed.unrealized_pnl == pytest.approx(reference.unrealized_pnl)
    assert resumed.net_pnl == pytest.approx(reference.net_pnl)
    assert resumed.total_return == pytest.approx(reference.total_return)
    assert resumed.fill_count == reference.fill_count

    reference_ledger = create_universal_ledger(tmp_path / "reference.db")
    recovery_ledger = create_universal_ledger(tmp_path / "recovery.db")
    try:
        assert recovery_ledger.run("worker-recovery")["status"] == "COMPLETED"
        ref_events = reference_ledger.events("worker-reference", limit=100)
        recovery_events = recovery_ledger.events("worker-recovery", limit=100)
        assert [(row["event_type"], row["timestamp_ns"]) for row in recovery_events] == [
            (row["event_type"], row["timestamp_ns"]) for row in ref_events
        ]
        ref_fills = reference_ledger.fills("worker-reference", limit=100)
        recovery_fills = recovery_ledger.fills("worker-recovery", limit=100)
        assert [(row.order_id, row.sequence, row.quantity, row.price) for row in recovery_fills] == [
            (row.order_id, row.sequence, row.quantity, row.price) for row in ref_fills
        ]
        assert recovery_ledger.count_events("worker-recovery") == reference_ledger.count_events("worker-reference")
        assert recovery_ledger.count_fills("worker-recovery") == reference_ledger.count_fills("worker-reference")
        assert recovery_ledger.count_trades("worker-recovery") == reference_ledger.count_trades("worker-reference")
        assert recovery_ledger.count_equity("worker-recovery") == reference_ledger.count_equity("worker-reference")
    finally:
        reference_ledger.close()
        recovery_ledger.close()
