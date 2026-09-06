import pytest

from app.backtesting.ledger import BacktestLedger, Checkpoint, LedgerRecord


def test_sqlite_ledger_persists_run_records_and_checkpoint(tmp_path):
    path = tmp_path / "backtest.sqlite"
    ledger = BacktestLedger(str(path))
    ledger.start_run("run-1", "cash-future", "1.0", 10_000_000, strategy_hash="abc", metadata={"seed": 42})
    ledger.append(LedgerRecord("run-1", "FILL", 100, {"order_id": "o1", "price": 101.5}))
    ledger.checkpoint(Checkpoint("run-1", 7, 100, {"cash": 9_999_000, "position": 10}))
    ledger.close()

    reopened = BacktestLedger(str(path))
    assert reopened.run_metadata("run-1")["initial_capital"] == 10_000_000
    assert reopened.records("run-1")[0].payload["price"] == 101.5
    checkpoint = reopened.load_checkpoint("run-1")
    assert checkpoint.event_index == 7
    assert checkpoint.state["position"] == 10
    reopened.close()


def test_checkpoint_is_updated_atomically():
    ledger = BacktestLedger()
    ledger.start_run("run-2", "s", "1", 1000)
    ledger.checkpoint(Checkpoint("run-2", 1, 10, {"x": 1}))
    ledger.checkpoint(Checkpoint("run-2", 2, 20, {"x": 2}))
    assert ledger.load_checkpoint("run-2").state["x"] == 2
    history = ledger.checkpoint_history("run-2")
    assert [(c.event_index, c.timestamp_ns) for c in history] == [(1, 10), (2, 20)]
    ledger.close()


def test_checkpoint_history_survives_reopen(tmp_path):
    path = tmp_path / "checkpoint-history.sqlite"
    ledger = BacktestLedger(str(path))
    ledger.start_run("run-history", "s", "1", 1000)
    ledger.checkpoint(Checkpoint("run-history", 1, 10, {"x": 1}))
    ledger.checkpoint(Checkpoint("run-history", 2, 20, {"x": 2}))
    ledger.close()

    reopened = BacktestLedger(str(path))
    history = reopened.checkpoint_history("run-history")
    assert [c.state["x"] for c in history] == [1, 2]
    assert reopened.load_checkpoint("run-history").state["x"] == 2
    reopened.close()


def test_duplicate_run_id_is_rejected():
    ledger = BacktestLedger()
    ledger.start_run("run-3", "s", "1", 1000)
    with pytest.raises(ValueError, match="already exists"):
        ledger.start_run("run-3", "s", "2", 2000)
    ledger.close()


def test_records_and_checkpoints_require_existing_run():
    ledger = BacktestLedger()
    with pytest.raises(ValueError, match="unknown run_id"):
        ledger.append(LedgerRecord("missing", "FILL", 1, {}))
    with pytest.raises(ValueError, match="unknown run_id"):
        ledger.checkpoint(Checkpoint("missing", 1, 1, {}))
    ledger.close()


def test_append_batch_is_atomic_and_persists_all_records():
    ledger = BacktestLedger()
    ledger.start_run("run-4", "s", "1", 1000)
    count = ledger.append_batch(
        LedgerRecord("run-4", "EVENT", i, {"value": i}) for i in range(3)
    )
    assert count == 3
    assert [r.payload["value"] for r in ledger.records("run-4")] == [0, 1, 2]
    ledger.close()


def test_append_batch_rolls_back_when_any_record_is_invalid():
    ledger = BacktestLedger()
    ledger.start_run("run-5", "s", "1", 1000)
    with pytest.raises(ValueError, match="timestamp_ns cannot be negative"):
        ledger.append_batch([
            LedgerRecord("run-5", "EVENT", 1, {"value": 1}),
            LedgerRecord("run-5", "EVENT", -1, {"value": 2}),
        ])
    assert ledger.records("run-5") == ()
    ledger.close()
