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
    ledger.close()
