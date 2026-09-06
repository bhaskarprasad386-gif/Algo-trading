import io

from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.backtesting.ledger_export import export_ledger_csv


def test_ledger_csv_export_has_stable_core_columns_and_payload_fields():
    ledger = BacktestLedger()
    ledger.start_run("run-export", "cash-future", "1", 1_000_000)
    ledger.append(LedgerRecord("run-export", "CASH_FUTURE_TRADE", 100, {"net_pnl": 210, "future_instrument": "NFO:101"}))
    output = io.StringIO()

    assert export_ledger_csv(ledger, "run-export", output) == 1
    rows = output.getvalue().splitlines()
    assert rows[0].startswith("run_id,record_type,timestamp_ns,future_instrument,net_pnl")
    assert "run-export,CASH_FUTURE_TRADE,100,NFO:101,210" in rows[1]


def test_ledger_csv_export_can_filter_record_type():
    ledger = BacktestLedger()
    ledger.start_run("run-filter", "strategy", "1", 1000)
    ledger.append(LedgerRecord("run-filter", "TRADE", 1, {"pnl": 10}))
    ledger.append(LedgerRecord("run-filter", "CHECKPOINT", 2, {"state": {"x": 1}}))
    output = io.StringIO()

    assert export_ledger_csv(ledger, "run-filter", output, record_type="TRADE") == 1
    assert "CHECKPOINT" not in output.getvalue()
    assert "TRADE" in output.getvalue()
