from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter, RECORD_TYPE
from app.backtesting.high_resolution_pnl import HighResolutionTrade
from app.backtesting.ledger import BacktestLedger


def _trade(instrument: str, entry_ts: int, exit_ts: int, pnl: float) -> HighResolutionTrade:
    return HighResolutionTrade(
        instrument=instrument,
        quantity=2,
        entry_timestamp_ns=entry_ts,
        exit_timestamp_ns=exit_ts,
        entry_price=100.0,
        exit_price=100.0 + pnl / 2,
        gross_pnl=pnl,
        fees=1.0,
    )


def test_high_resolution_trade_is_persisted_incrementally():
    ledger = BacktestLedger()
    ledger.start_run("hr-1", "strategy", "1", 1000.0)
    writer = HighResolutionLedgerWriter(ledger, "hr-1")

    writer.append(_trade("NIFTY", 1_000_001, 1_000_009, 6.0))

    records = ledger.records("hr-1", RECORD_TYPE)
    assert len(records) == 1
    assert records[0].timestamp_ns == 1_000_009
    assert records[0].payload["entry_timestamp_ns"] == 1_000_001
    assert records[0].payload["exit_timestamp_ns"] == 1_000_009
    assert records[0].payload["net_pnl"] == 5.0
    ledger.close()


def test_high_resolution_trade_batch_persists_atomically():
    ledger = BacktestLedger()
    ledger.start_run("hr-2", "strategy", "1", 1000.0)
    writer = HighResolutionLedgerWriter(ledger, "hr-2")

    count = writer.append_many((
        _trade("NIFTY", 10, 20, 6.0),
        _trade("BANKNIFTY", 30, 40, 8.0),
    ))

    assert count == 2
    records = ledger.records("hr-2", RECORD_TYPE)
    assert [r.payload["instrument"] for r in records] == ["NIFTY", "BANKNIFTY"]
    assert sum(r.payload["net_pnl"] for r in records) == 12.0
    ledger.close()


def test_writer_rejects_invalid_trade_without_persisting():
    ledger = BacktestLedger()
    ledger.start_run("hr-3", "strategy", "1", 1000.0)
    writer = HighResolutionLedgerWriter(ledger, "hr-3")
    invalid = _trade("NIFTY", 20, 10, 6.0)

    try:
        writer.append(invalid)
    except ValueError as exc:
        assert "exit timestamp" in str(exc)
    else:
        raise AssertionError("expected invalid timestamp rejection")

    assert ledger.records("hr-3", RECORD_TYPE) == ()
    ledger.close()
