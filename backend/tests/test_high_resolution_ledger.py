from app.backtesting.high_resolution_ledger import HIGH_RESOLUTION_TRADE_RECORD, HighResolutionLedgerWriter
from app.backtesting.high_resolution_pnl import HighResolutionTrade
from app.backtesting.ledger import BacktestLedger


def trade(ts: int, pnl: float) -> HighResolutionTrade:
    return HighResolutionTrade(
        instrument="NIFTY-FUT",
        quantity=2,
        entry_timestamp_ns=ts - 5,
        exit_timestamp_ns=ts,
        entry_price=100.0,
        exit_price=100.0 + pnl / 2,
        gross_pnl=pnl,
        fees=0.5,
    )


def test_high_resolution_trade_is_persisted_with_timestamps_and_net_pnl(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "ledger.db"))
    ledger.start_run("run-1", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "run-1")

    writer.append(trade(1_000_009, 6.0))

    records = writer.records()
    assert len(records) == 1
    record = records[0]
    assert record.record_type == HIGH_RESOLUTION_TRADE_RECORD
    assert record.timestamp_ns == 1_000_009
    assert record.payload["entry_timestamp_ns"] == 1_000_004
    assert record.payload["exit_timestamp_ns"] == 1_000_009
    assert record.payload["net_pnl"] == 5.5
    ledger.close()


def test_high_resolution_trades_append_incrementally(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "ledger.db"))
    ledger.start_run("run-2", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "run-2")

    assert writer.append_many(trade(ts, float(ts)) for ts in (6, 7, 8)) == 3
    records = ledger.records("run-2", HIGH_RESOLUTION_TRADE_RECORD)
    assert [r.timestamp_ns for r in records] == [6, 7, 8]
    assert [r.payload["net_pnl"] for r in records] == [5.5, 6.5, 7.5]
    ledger.close()


def test_writer_requires_existing_run(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "ledger.db"))
    writer = HighResolutionLedgerWriter(ledger, "missing")
    try:
        writer.append(trade(10, 4.0))
    except ValueError as exc:
        assert "unknown run_id" in str(exc)
    else:
        raise AssertionError("expected unknown run_id")
    ledger.close()
