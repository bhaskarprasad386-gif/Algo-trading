"""Durable audit-event regressions for the isolated Box Spread paper ledger."""

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount,
    BoxSpreadPaperAuditEvent,
    BoxSpreadPaperEntryClaim,
    BoxSpreadPaperLedger,
    BoxSpreadPaperLedgerEntry,
    BoxSpreadPaperTradeRecord,
)


def _ledger():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperEntryClaim.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
        BoxSpreadPaperAuditEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    return engine, BoxSpreadPaperLedger(sessionmaker(bind=engine, expire_on_commit=False))


def _trade(ledger, account_key, idempotency_key):
    return ledger.create_trade(
        account_key=account_key,
        idempotency_key=idempotency_key,
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


def test_validation_claim_crash_and_operator_decision_are_append_only():
    engine, ledger = _ledger()
    try:
        trade = _trade(ledger, "audit-recovery", "audit-recovery-entry")
        ledger.record_entry_validation_failure(
            trade_id=trade.id,
            account_key="audit-recovery",
            reason="Fresh four-leg depth unavailable",
            details={"http_status": 409},
        )
        ledger.claim_entry_attempt(trade_id=trade.id, account_key="audit-recovery")
        ledger.record_manual_review_required(
            trade_id=trade.id,
            account_key="audit-recovery",
            reason="Worker failed after durable claim",
            failure_type="RuntimeError",
        )
        ledger.record_manual_review_decision(
            trade_id=trade.id,
            account_key="audit-recovery",
            decision="ACKNOWLEDGED",
            actor="box-spread-paper-owner",
            reason="Operator inspected the durable claim and fills",
        )

        events = ledger.list_audit_events(
            trade_id=trade.id, account_key="audit-recovery",
        )
        assert [event["event_type"] for event in events] == [
            "ENTRY_VALIDATION_FAILED",
            "ENTRY_CLAIMED",
            "MANUAL_REVIEW_REQUIRED",
            "MANUAL_REVIEW_DECISION",
        ]
        assert events[0]["from_state"] == events[0]["to_state"] == "CREATED"
        assert events[1]["to_state"] == "CLAIMED"
        assert events[2]["to_state"] == "MANUAL_REVIEW_REQUIRED"
        assert events[3]["actor"] == "box-spread-paper-owner"
        assert events[3]["to_state"] == "ACKNOWLEDGED"

        # An audit decision records evidence only; it must not release the claim,
        # mark the trade complete, or permit automatic replay.
        assert ledger.get_entry_claim(
            trade_id=trade.id, account_key="audit-recovery",
        )["status"] == "CLAIMED"
        recovered = ledger.list_open_trades(account_key="audit-recovery")
        assert recovered[0]["manual_review_required"] is True
        assert recovered[0]["automatic_replay"] is False
        with pytest.raises(ValueError, match="paper trade not found for account"):
            ledger.list_audit_events(
                trade_id=trade.id, account_key="another-account",
            )
    finally:
        engine.dispose()


def test_completed_four_leg_basket_and_completion_event_commit_together():
    engine, ledger = _ledger()
    try:
        trade = _trade(ledger, "audit-completed", "audit-completed-entry")
        ledger.claim_entry_attempt(trade_id=trade.id, account_key="audit-completed")
        receipts = ledger.record_entry_basket(
            trade_id=trade.id,
            account_key="audit-completed",
            legs={
                "LOW_CE": {"fill_id": "audit-low-ce", "action": "BUY", "quantity": 25, "fill_price": 100},
                "HIGH_CE": {"fill_id": "audit-high-ce", "action": "SELL", "quantity": 25, "fill_price": 90},
                "HIGH_PE": {"fill_id": "audit-high-pe", "action": "BUY", "quantity": 25, "fill_price": 80},
                "LOW_PE": {"fill_id": "audit-low-pe", "action": "SELL", "quantity": 25, "fill_price": 70},
            },
        )
        assert len(receipts) == 4
        events = ledger.list_audit_events(
            trade_id=trade.id, account_key="audit-completed",
        )
        assert [event["event_type"] for event in events] == [
            "ENTRY_CLAIMED", "ENTRY_COMPLETED",
        ]
        assert events[-1]["from_state"] == "CLAIMED"
        assert events[-1]["to_state"] == "COMPLETED"
        assert events[-1]["details"] == {"leg_count": 4, "quantity_per_leg": 25}
        assert ledger.get_entry_claim(
            trade_id=trade.id, account_key="audit-completed",
        )["status"] == "COMPLETED"
    finally:
        engine.dispose()
