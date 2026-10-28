"""Restart regression tests for the isolated Box Spread paper-entry ledger."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.scanner import box_spread_paper_routes as routes
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount,
    BoxSpreadPaperEntryClaim,
    BoxSpreadPaperLedger,
    BoxSpreadPaperLedgerEntry,
    BoxSpreadPaperTradeRecord,
)


def test_restart_after_partial_entry_fill_blocks_route_replay(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
        BoxSpreadPaperEntryClaim.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        before_restart = BoxSpreadPaperLedger(factory)
        trade = before_restart.create_trade(
            account_key="restart-partial",
            idempotency_key="restart-partial-entry",
            underlying="NIFTY",
            instrument_class="INDEX",
            expiry="2026-10-29",
            low_strike=25000,
            high_strike=25100,
            direction="LONG",
            lots=1,
            lot_size=25,
        )
        before_restart.claim_entry_attempt(
            trade_id=trade.id, account_key="restart-partial",
        )
        before_restart.record_fill(
            trade_id=trade.id,
            account_key="restart-partial",
            fill_id="durable-low-ce",
            role="LOW_CE",
            action="BUY",
            quantity=25,
            fill_price=100,
        )

        # Simulate a new API process by constructing a fresh ledger object over
        # the same persistent database. The in-memory engine state is irrelevant.
        after_restart = BoxSpreadPaperLedger(factory)
        assert after_restart.get_entry_claim(
            trade_id=trade.id, account_key="restart-partial",
        )["status"] == "CLAIMED"
        # A second ledger instance (as in a separate API worker) cannot claim
        # the same durable entry attempt again.
        duplicate_claim = after_restart.claim_entry_attempt(
            trade_id=trade.id, account_key="restart-partial",
        )
        assert duplicate_claim["created"] is False
        assert duplicate_claim["status"] == "CLAIMED"

        recovery = after_restart.list_open_trades(account_key="restart-partial")
        assert len(recovery) == 1
        recovered_trade = recovery[0]
        assert recovered_trade["trade_id"] == trade.id
        assert recovered_trade["instrument_class"] == "INDEX"
        assert recovered_trade["low_strike"] == 25000.0
        assert recovered_trade["high_strike"] == 25100.0
        assert recovered_trade["direction"] == "LONG"
        assert recovered_trade["lots"] == 1
        assert recovered_trade["lot_size"] == 25
        assert recovered_trade["manual_review_required"] is True
        assert recovered_trade["automatic_replay"] is False
        assert recovered_trade["legs"][0]["role"] == "LOW_CE"
        assert recovered_trade["legs"][0]["entry_filled_quantity"] == 25
        assert recovered_trade["legs"][0]["open_position_action"] == "BUY"
        assert recovered_trade["legs"][0]["open_position_quantity"] == 25

        recovered_fills = after_restart.list_fills(
            trade.id, account_key="restart-partial",
        )
        assert [(fill.fill_id, fill.role, fill.quantity) for fill in recovered_fills] == [
            ("durable-low-ce", "LOW_CE", 25),
        ]

        request = routes.CreatePaperTrade(
            underlying="NIFTY",
            instrument_class="INDEX",
            expiry="2026-10-29",
            low_strike=25000,
            high_strike=25100,
            direction="LONG",
            lots=1,
            idempotency_key="restart-partial-entry",
            account_key="restart-partial",
        )
        # This test targets durable replay protection, not the independent
        # manual execution switch. Enable the switch to reach the ledger guard.
        monkeypatch.setattr(routes, "_paper_execution_enabled", True)
        monkeypatch.setattr(routes, "_ledger", after_restart)
        monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)

        def forbidden(*args, **kwargs):
            pytest.fail("restart recovery must block before resolving contracts or reading quotes")

        monkeypatch.setattr(routes, "_resolve", forbidden)
        monkeypatch.setattr(routes, "live_box_spread_opportunities", forbidden)

        with pytest.raises(routes.HTTPException) as raised:
            asyncio.run(routes.simulate_entry(
                trade.id, routes.SimulateEntry(account_key="restart-partial"),
            ))

        assert raised.value.status_code == 409
        assert "durably claimed" in raised.value.detail
        # A blocked replay must leave the original partial state untouched.
        assert len(after_restart.list_fills(
            trade.id, account_key="restart-partial",
        )) == 1
        assert after_restart.get_entry_claim(
            trade_id=trade.id, account_key="restart-partial",
        )["status"] == "CLAIMED"
    finally:
        engine.dispose()
