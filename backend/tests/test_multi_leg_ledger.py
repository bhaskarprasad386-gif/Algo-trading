from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.ledger import BacktestLedger
from app.backtesting.multi_leg_pnl import build_basket_pnl
from app.backtesting.multi_leg_ledger import MultiLegLedgerWriter


def _basket(signal_id: str):
    entries = {
        f"{signal_id}:a": SimFill(f"{signal_id}:a", "A", ExecutionSide.BUY, 1, 10.0, 1),
    }
    exits = {
        f"{signal_id}:a": SimFill(f"{signal_id}:a", "A", ExecutionSide.BUY, 1, 12.0, 2),
    }
    return build_basket_pnl(signal_id, entries, exits)


def test_multi_leg_baskets_persist_incrementally_without_ram_accumulation():
    ledger = BacktestLedger()
    ledger.start_run("run-1", "generic", "1", 100_000)
    writer = MultiLegLedgerWriter(ledger)

    assert writer.append_many("run-1", [_basket("b1"), _basket("b2")], 200) == 2
    records = ledger.records("run-1", "multi_leg_basket")
    assert len(records) == 2
    assert [record.payload["signal_id"] for record in records] == ["b1", "b2"]
    assert [record.payload["net_pnl"] for record in records] == [2.0, 2.0]
