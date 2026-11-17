"""PostgreSQL multi-worker concurrency conformance for Box Paper baskets.

Uses independent SQLAlchemy connections and PostgreSQL row locks. Skips when
IV_TEST_POSTGRES_URL is not configured.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount, BoxSpreadPaperAuditEvent, BoxSpreadPaperEntryClaim,
    BoxSpreadPaperLedger, BoxSpreadPaperLedgerEntry, BoxSpreadPaperTradeRecord,
)


@pytest.fixture
def postgres_paper_ledger():
    url = os.getenv("IV_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("IV_TEST_POSTGRES_URL is not configured")
    admin_engine = create_engine(url, pool_pre_ping=True)
    schema = f"box_paper_concurrency_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True, pool_size=5, max_overflow=2,
    )
    tables = [
        BoxSpreadPaperAccount.__table__, BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__, BoxSpreadPaperEntryClaim.__table__,
        BoxSpreadPaperAuditEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = BoxSpreadPaperLedger(factory)
    try:
        yield ledger
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _trade(ledger, account_key):
    return ledger.create_trade(
        account_key=account_key, idempotency_key=f"pg-concurrency-{account_key}",
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG",
        lots=1, lot_size=25, alert_edge=10,
    )


def _entry_legs():
    return {
        "LOW_CE": {"fill_id": "ENTRY:pg:LOW_CE", "action": "BUY", "quantity": 25, "fill_price": "100"},
        "HIGH_CE": {"fill_id": "ENTRY:pg:HIGH_CE", "action": "SELL", "quantity": 25, "fill_price": "60"},
        "HIGH_PE": {"fill_id": "ENTRY:pg:HIGH_PE", "action": "BUY", "quantity": 25, "fill_price": "70"},
        "LOW_PE": {"fill_id": "ENTRY:pg:LOW_PE", "action": "SELL", "quantity": 25, "fill_price": "30"},
    }


def _exit_legs():
    return {
        role: {"quantity": 25, "fill_price": "101"}
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
    }


def test_concurrent_workers_create_exactly_one_durable_entry_claim(postgres_paper_ledger):
    ledger = postgres_paper_ledger
    trade = _trade(ledger, "pg-claim-race")
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=10)
        return ledger.claim_entry_attempt(trade_id=trade.id, account_key="pg-claim-race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))

    assert sum(bool(item["created"]) for item in outcomes) == 1
    assert {item["status"] for item in outcomes} == {"CLAIMED"}
    assert ledger.get_entry_claim(trade_id=trade.id, account_key="pg-claim-race")["status"] == "CLAIMED"
    events = ledger.list_audit_events(trade_id=trade.id, account_key="pg-claim-race")
    assert sum(item["event_type"] == "ENTRY_CLAIMED" for item in events) == 1


def test_concurrent_full_basket_exits_cannot_close_same_position_twice(postgres_paper_ledger):
    ledger = postgres_paper_ledger
    account_key = "pg-exit-race"
    trade = _trade(ledger, account_key)
    assert ledger.claim_entry_attempt(trade_id=trade.id, account_key=account_key)["created"]
    assert len(ledger.record_entry_basket(
        trade_id=trade.id, account_key=account_key, legs=_entry_legs(),
    )) == 4
    barrier = Barrier(2)

    def close(fill_id):
        barrier.wait(timeout=10)
        try:
            receipts = ledger.record_exit_basket(
                trade_id=trade.id, fill_id=fill_id, account_key=account_key,
                legs=_exit_legs(),
            )
            return ("accepted", len(receipts))
        except ValueError as exc:
            return ("rejected", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(close, ("pg-exit-race-a", "pg-exit-race-b")))

    assert sum(result[0] == "accepted" for result in outcomes) == 1
    assert sum(result[0] == "rejected" for result in outcomes) == 1
    fills = ledger.list_fills(trade.id, account_key=account_key)
    assert len(fills) == 8
    assert sum(item.fill_id.startswith("EXIT:BASKET:") for item in fills) == 4
    assert ledger.get_trade_summary(trade.id, account_key=account_key)["status"] == "CLOSED"
