from __future__ import annotations

import sqlite3

import pytest

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.result_ledger import (
    BacktestEvent,
    BacktestFill,
    BacktestResultLedger,
    BacktestTrade,
    EquityPoint,
)


def trade(trade_id: str = "t1", sequence: int = 1, net: float = 95.0) -> BacktestTrade:
    return BacktestTrade(
        trade_id=trade_id,
        sequence=sequence,
        timestamp_ns=1_000 + sequence,
        instrument="NIFTY",
        side="BUY",
        quantity=1,
        entry_price=100.0,
        exit_price=200.0,
        gross_pnl=100.0,
        fees=3.0,
        slippage=2.0,
        net_pnl=net,
        contract="NIFTY26SEP25000CE",
        expiry="2026-09-24",
        strike=25000,
        leg="CALL_LONG",
        data_resolution="s",
        metadata={"strategy": "box"},
    )


def fill(fill_id: str = "f1", sequence: int = 1, price: float = 101.0) -> BacktestFill:
    return BacktestFill(
        fill_id=fill_id,
        order_id="order-1",
        sequence=sequence,
        timestamp_ns=2_000 + sequence,
        instrument="NIFTY26SEP25000CE",
        side="BUY",
        quantity=2,
        price=price,
        fee=1.5,
        metadata={"leg": "CALL_LONG"},
    )


def test_incremental_fill_append_and_idempotency() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-fills", {"strategy_id": "multi_leg"})

    assert ledger.append_fills("run-fills", [fill()]) == 1
    assert ledger.append_fills("run-fills", [fill()]) == 0
    rows = ledger.fills("run-fills")
    assert len(rows) == 1
    assert rows[0]["fill_id"] == "f1"
    assert rows[0]["order_id"] == "order-1"
    assert rows[0]["quantity"] == 2
    assert rows[0]["price"] == 101.0


def test_conflicting_fill_identity_is_rejected() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-fill-conflict", {"strategy_id": "multi_leg"})
    ledger.append_fills("run-fill-conflict", [fill()])

    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_fills("run-fill-conflict", [fill(price=102.0)])


def test_same_order_can_have_multiple_fills() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-partial-fills", {"strategy_id": "depth"})
    assert ledger.append_fills(
        "run-partial-fills",
        [fill("f1", 1, 101.0), fill("f2", 2, 102.0)],
    ) == 2
    assert [row["fill_id"] for row in ledger.fills("run-partial-fills")] == ["f1", "f2"]


def test_incremental_append_and_idempotency() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "box", "resolution": "s"})

    assert ledger.append_events("run-a", [BacktestEvent(1, 1000, "SIGNAL", {"side": "BUY"})]) == 1
    assert ledger.append_trades("run-a", [trade()]) == 1
    assert ledger.append_equity("run-a", [EquityPoint(1000, 100095, 95, 0, 0)]) == 1

    assert ledger.append_events("run-a", [BacktestEvent(1, 1000, "SIGNAL", {"side": "BUY"})]) == 0
    assert ledger.append_trades("run-a", [trade()]) == 0
    assert ledger.append_equity("run-a", [EquityPoint(1000, 100095, 95, 0, 0)]) == 0

    assert len(ledger.events("run-a")) == 1
    assert len(ledger.trades("run-a")) == 1
    assert len(ledger.equity("run-a")) == 1


def test_conflicting_duplicates_are_rejected() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "synthetic_cash_carry"})
    ledger.append_trades("run-a", [trade()])
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_trades("run-a", [trade(net=94.0)])

    ledger.append_events("run-a", [BacktestEvent(2, 1002, "ENTRY", {"price": 10})])
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_events("run-a", [BacktestEvent(2, 1002, "ENTRY", {"price": 11})])


def test_runs_are_isolated_and_cursor_is_incremental() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "strategy_a"})
    ledger.create_run("run-b", {"strategy_id": "strategy_b"})
    ledger.append_trades("run-a", [trade("a1", 1), trade("a2", 2)])
    ledger.append_trades("run-b", [trade("b1", 1)])

    page = ledger.trades("run-a", limit=1)
    assert [row["trade_id"] for row in page] == ["a1"]
    next_page = ledger.trades("run-a", after_sequence=page[-1]["sequence"])
    assert [row["trade_id"] for row in next_page] == ["a2"]
    assert [row["trade_id"] for row in ledger.trades("run-b")] == ["b1"]


def test_unknown_run_is_rejected() -> None:
    ledger = BacktestResultLedger()
    with pytest.raises(ValueError, match="unknown run"):
        ledger.trades("missing")


def test_same_timestamp_equity_points_are_distinct_records() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-same-ts", {"strategy_id": "same_timestamp_equity"})

    points = [
        EquityPoint(1_000, 100_000.0, 0.0, 0.0, 0.0),
        EquityPoint(1_000, 100_001.0, 1.0, 0.0, 0.0),
    ]

    assert ledger.append_equity("run-same-ts", points) == 2
    assert len(ledger.equity("run-same-ts")) == 2


def test_same_timestamp_equity_pagination_is_not_ambiguous() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-equity-page", {"strategy_id": "same_timestamp_pagination"})

    points = [
        EquityPoint(2_000, 100_000.0, 0.0, 0.0, 0.0),
        EquityPoint(2_000, 100_001.0, 1.0, 0.0, 0.0),
        EquityPoint(2_001, 100_002.0, 2.0, 0.0, 0.0),
    ]

    assert ledger.append_equity("run-equity-page", points) == 3
    page = ledger.equity("run-equity-page", limit=1)
    assert len(page) == 1
    next_page = ledger.equity(
        "run-equity-page",
        limit=1,
        after_timestamp_ns=page[-1]["timestamp_ns"],
        after_equity_id=page[-1]["equity_id"],
    )
    assert len(next_page) == 1
    assert next_page[-1]["timestamp_ns"] == 2_000
    assert next_page[-1]["equity_id"] > page[-1]["equity_id"]

    final_page = ledger.equity(
        "run-equity-page",
        limit=1,
        after_timestamp_ns=next_page[-1]["timestamp_ns"],
        after_equity_id=next_page[-1]["equity_id"],
    )
    assert len(final_page) == 1
    assert final_page[-1]["timestamp_ns"] == 2_001


def test_identical_equity_record_is_idempotent() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-equity-idempotent", {"strategy_id": "equity_idempotent"})
    point = EquityPoint(3_000, 100_000.0, 0.0, 0.0, 0.0)

    assert ledger.append_equity("run-equity-idempotent", [point]) == 1
    assert ledger.append_equity("run-equity-idempotent", [point]) == 0
    assert len(ledger.equity("run-equity-idempotent")) == 1


def test_conflicting_same_equity_identity_is_rejected() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-equity-conflict", {"strategy_id": "equity_conflict"})
    ledger.append_equity(
        "run-equity-conflict",
        [EquityPoint(4_000, 100_000.0, 0.0, 0.0, 0.0)],
    )

    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_equity(
            "run-equity-conflict",
            [EquityPoint(4_000, 100_001.0, 1.0, 0.0, 0.0)],
        )


def test_legacy_equity_schema_is_migratable(tmp_path) -> None:
    db_path = tmp_path / "legacy-ledger.sqlite"
    import sqlite3

    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE backtest_runs (
            run_id TEXT PRIMARY KEY,
            provenance_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CREATED',
            created_at_ns INTEGER NOT NULL
        );
        CREATE TABLE backtest_equity (
            run_id TEXT NOT NULL,
            timestamp_ns INTEGER NOT NULL,
            equity REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            drawdown REAL NOT NULL,
            PRIMARY KEY (run_id, timestamp_ns),
            FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
        );
        """
    )
    db.execute(
        "INSERT INTO backtest_runs(run_id, provenance_json, status, created_at_ns) VALUES (?, ?, ?, ?)",
        ("legacy-run", "{}", "COMPLETED", 0),
    )
    db.execute(
        "INSERT INTO backtest_equity(run_id, timestamp_ns, equity, realized_pnl, unrealized_pnl, drawdown) VALUES (?, ?, ?, ?, ?, ?)",
        ("legacy-run", 1_000, 100_000.0, 0.0, 0.0, 0.0),
    )
    db.commit()
    db.close()

    ledger = BacktestResultLedger(str(db_path))

    rows = ledger.equity("legacy-run")
    assert len(rows) == 1
    assert rows[0]["timestamp_ns"] == 1_000

    assert ledger.append_equity(
        "legacy-run",
        [EquityPoint(1_000, 100_001.0, 1.0, 0.0, 0.0)],
    ) == 1


def test_fill_sequence_must_be_unique_per_run():
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run("run-sequence", {"strategy_id": "test"})

    first = fill("f1", sequence=7, price=101.0)
    second = fill("f2", sequence=7, price=102.0)

    assert ledger.append_fills("run-sequence", [first]) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_fills("run-sequence", [second])


def test_composed_transaction_rolls_back_ledger_and_checkpoint_together() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-atomic", {"strategy_id": "atomic"})
    checkpoints = CheckpointStore(ledger.connection)
    point = EquityPoint(5_000, 100_010.0, 10.0, 0.0, 0.0)

    with pytest.raises(RuntimeError, match="abort"):
        with ledger.transaction():
            ledger.append_equity("run-atomic", [point])
            checkpoints.save(
                ReplayCheckpoint("run-atomic", 5_000, 1, 1, 10.0, {"portfolio": {"cash": 100010}}),
                commit=False,
            )
            raise RuntimeError("abort")

    assert ledger.equity("run-atomic") == []
    assert checkpoints.load("run-atomic") is None


def test_composed_transaction_commits_ledger_and_checkpoint_together() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-atomic-commit", {"strategy_id": "atomic"})
    checkpoints = CheckpointStore(ledger.connection)
    point = EquityPoint(6_000, 100_020.0, 20.0, 0.0, 0.0)

    with ledger.transaction():
        ledger.append_equity("run-atomic-commit", [point])
        checkpoints.save(
            ReplayCheckpoint("run-atomic-commit", 6_000, 1, 1, 20.0, {"portfolio": {"cash": 100020}}),
            commit=False,
        )

    assert len(ledger.equity("run-atomic-commit")) == 1
    checkpoint = checkpoints.load("run-atomic-commit")
    assert checkpoint is not None
    assert checkpoint.sequence == 1
