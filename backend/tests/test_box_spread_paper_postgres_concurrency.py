"""PostgreSQL multi-worker safety tests for the Box Spread Paper ledger.

The CI backend job provides IV_TEST_POSTGRES_URL backed by PostgreSQL 16.
These tests deliberately use independent ledger instances and DB sessions so
SQLite/in-process locks cannot make the assertions pass accidentally.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount,
    BoxSpreadPaperEntryClaim,
    BoxSpreadPaperLedger,
    BoxSpreadPaperLedgerEntry,
    BoxSpreadPaperTradeRecord,
)


@pytest.fixture
def postgres_paper_ledgers():
    url = os.getenv("IV_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("IV_TEST_POSTGRES_URL is not configured")
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2)
    tables = [
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
        BoxSpreadPaperEntryClaim.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    account_key = f"paper-pg-{uuid4().hex}"
    try:
        yield (
            account_key,
            BoxSpreadPaperLedger(factory),
            BoxSpreadPaperLedger(factory),
        )
    finally:
        with factory.begin() as session:
            session.execute(delete(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.account_key == account_key,
            ))
            session.execute(delete(BoxSpreadPaperEntryClaim).where(
                BoxSpreadPaperEntryClaim.account_key == account_key,
            ))
            session.execute(delete(BoxSpreadPaperTradeRecord).where(
                BoxSpreadPaperTradeRecord.account_key == account_key,
            ))
            session.execute(delete(BoxSpreadPaperAccount).where(
                BoxSpreadPaperAccount.account_key == account_key,
            ))
        engine.dispose()


def _create_trade(ledger, account_key):
    return ledger.create_trade(
        account_key=account_key,
        idempotency_key=f"{account_key}-entry",
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry="2026-11-26",
        low_strike=25000,
        high_strike=25100,
        direction="LONG",
        lots=1,
        lot_size=25,
        alert_edge=8,
    )


def _entry_legs(prefix):
    return {
        "LOW_CE": {"fill_id": f"{prefix}:LOW_CE", "action": "BUY", "quantity": 25, "fill_price": 100},
        "HIGH_CE": {"fill_id": f"{prefix}:HIGH_CE", "action": "SELL", "quantity": 25, "fill_price": 90},
        "HIGH_PE": {"fill_id": f"{prefix}:HIGH_PE", "action": "BUY", "quantity": 25, "fill_price": 80},
        "LOW_PE": {"fill_id": f"{prefix}:LOW_PE", "action": "SELL", "quantity": 25, "fill_price": 70},
    }


def _exit_legs():
    return {
        "LOW_CE": {"quantity": 25, "fill_price": 101},
        "HIGH_CE": {"quantity": 25, "fill_price": 89},
        "HIGH_PE": {"quantity": 25, "fill_price": 81},
        "LOW_PE": {"quantity": 25, "fill_price": 69},
    }


def test_postgres_duplicate_entry_claim_is_serialized_across_ledger_workers(postgres_paper_ledgers):
    account_key, first, second = postgres_paper_ledgers
    trade = _create_trade(first, account_key)
    barrier = Barrier(2)

    def claim(ledger):
        barrier.wait(timeout=5)
        return ledger.claim_entry_attempt(
            trade_id=trade.id, account_key=account_key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (first, second)))

    assert sum(bool(result["created"]) for result in results) == 1
    assert {result["status"] for result in results} == {"CLAIMED"}
    assert first.get_entry_claim(
        trade_id=trade.id, account_key=account_key,
    )["status"] == "CLAIMED"


def test_postgres_concurrent_exit_replay_is_idempotent_across_ledger_workers(postgres_paper_ledgers):
    account_key, first, second = postgres_paper_ledgers
    trade = _create_trade(first, account_key)
    first.claim_entry_attempt(trade_id=trade.id, account_key=account_key)
    first.record_entry_basket(
        trade_id=trade.id,
        legs=_entry_legs(f"{account_key}-entry"),
        account_key=account_key,
    )
    barrier = Barrier(2)

    def exit(ledger):
        barrier.wait(timeout=5)
        return ledger.record_exit_basket(
            trade_id=trade.id,
            fill_id=f"{account_key}-same-exit",
            legs=_exit_legs(),
            account_key=account_key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(exit, (first, second)))

    assert all(len(receipts) == 4 for receipts in results)
    assert [
        (receipt.fill_id, receipt.role, receipt.quantity, receipt.fill_price)
        for receipt in results[0]
    ] == [
        (receipt.fill_id, receipt.role, receipt.quantity, receipt.fill_price)
        for receipt in results[1]
    ]
    assert len(first.list_fills(trade.id, account_key=account_key)) == 8
    assert first.get_trade_summary(
        trade.id, account_key=account_key,
    )["status"] == "CLOSED"


def test_postgres_distinct_concurrent_exit_cannot_close_same_basket_twice(postgres_paper_ledgers):
    account_key, first, second = postgres_paper_ledgers
    trade = _create_trade(first, account_key)
    first.claim_entry_attempt(trade_id=trade.id, account_key=account_key)
    first.record_entry_basket(
        trade_id=trade.id,
        legs=_entry_legs(f"{account_key}-entry"),
        account_key=account_key,
    )
    barrier = Barrier(2)

    def exit(ledger, fill_id):
        barrier.wait(timeout=5)
        try:
            return ledger.record_exit_basket(
                trade_id=trade.id,
                fill_id=fill_id,
                legs=_exit_legs(),
                account_key=account_key,
            )
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda item: exit(*item),
            ((first, f"{account_key}-exit-a"), (second, f"{account_key}-exit-b")),
        ))

    successes = [result for result in results if isinstance(result, list)]
    failures = [result for result in results if isinstance(result, ValueError)]
    assert len(successes) == 1
    assert len(successes[0]) == 4
    assert len(failures) == 1
    assert "closed" in str(failures[0]).lower()
    assert len(first.list_fills(trade.id, account_key=account_key)) == 8
