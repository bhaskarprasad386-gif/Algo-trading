"""Regression coverage for paper risk-gate ordering and terminal capital semantics."""

from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db, *, paper_amount=50000.0, max_daily=0.0, max_positions=5, max_loss=0.0):
    db.add(GlobalPaperSetting(
        user_id=1,
        enabled=True,
        paper_amount=paper_amount,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=1,
        strategy_id="cash-future",
        min_gross_profit=0.0,
        mobile_number="",
        whatsapp_enabled=False,
        enabled=True,
        max_daily_capital=max_daily,
        max_simultaneous_positions=max_positions,
        max_loss=max_loss,
    ))
    db.commit()


def _event(event_id, *, capital=60000.0, lots=2):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="risk",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": lots,
                "edge": 5,
                "capital_used": capital,
            },
        },
    )


def test_partial_allocation_is_applied_before_daily_capital_gate(db_session):
    _setup(db_session, paper_amount=50000.0, max_daily=30000.0)

    # Request is 2 lots / 60k, but only 50k is globally available.
    # One lot is therefore the effective 30k allocation, which exactly fits
    # the 30k daily-cap boundary.
    assert AlertService().dispatch(
        db_session, _event("PARTIAL-BEFORE-DAILY")
    ) == 0

    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].lots == 1
    assert rows[0].capital_used == 30000.0


def test_daily_cap_rejection_after_partial_calculation_leaves_no_reservation(db_session):
    _setup(db_session, paper_amount=50000.0, max_daily=20000.0)

    # Effective allocation would be one 30k lot, but the daily budget is
    # smaller than that effective allocation. Nothing may be persisted.
    assert AlertService().dispatch(
        db_session, _event("DAILY-REJECT-NO-RESERVATION")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0


def test_max_loss_rejection_after_partial_and_daily_gates_leaves_no_reservation(db_session):
    _setup(db_session, paper_amount=50000.0, max_daily=30000.0, max_loss=100.0)

    svc = LivePaperTradeService()
    seed, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="LOSS-SEED",
        event_id="LOSS-SEED",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=10000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, seed, edge=0.0)
    db_session.commit()
    assert seed.unrealized_pnl == -100.0

    # Partial allocation -> 30k, daily-cap boundary -> allowed, then max-loss
    # rejects. There must be no phantom second reservation.
    assert AlertService().dispatch(
        db_session, _event("LOSS-REJECT-NO-RESERVATION")
    ) == 0

    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "LOSS-SEED"
    assert sum(float(row.capital_used) for row in rows) == 10000.0


def test_manual_close_releases_position_slot_but_not_daily_capital(db_session):
    _setup(db_session, paper_amount=100000.0, max_daily=30000.0, max_positions=1)

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="FIRST",
        event_id="FIRST-MANUAL",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.close(db_session, first, "MANUAL")
    assert first.status == "COMPLETED"
    assert first.exit_reason == "MANUAL"

    # Position slot is released, but the completed 30k still counts toward
    # today's daily-capital usage, so the second 30k entry must be blocked.
    assert AlertService().dispatch(
        db_session, _event("AFTER-MANUAL-DAILY-BLOCK", capital=30000, lots=1)
    ) == 0
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "AFTER-MANUAL-DAILY-BLOCK",
    ).all()
    assert rows == []


def test_expiry_close_releases_position_slot_but_retains_daily_capital_usage(db_session):
    _setup(db_session, paper_amount=100000.0, max_daily=30000.0, max_positions=1)

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="FIRST",
        event_id="FIRST-EXPIRY",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True

    closed = svc.close_expired(
        db_session, now=datetime(2026, 10, 4, 15, 30)
    )
    assert len(closed) == 1
    assert first.status == "COMPLETED"
    assert first.exit_reason == "EXPIRY_CLOSE"

    # Expiry frees max_simultaneous_positions, but it must not refund the
    # already-consumed daily capital budget.
    assert AlertService().dispatch(
        db_session, _event("AFTER-EXPIRY-DAILY-BLOCK", capital=30000, lots=1)
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "AFTER-EXPIRY-DAILY-BLOCK",
    ).count() == 0


def test_expiry_close_releases_global_reserved_capital_for_next_entry(db_session):
    _setup(db_session, paper_amount=30000.0, max_daily=60000.0, max_positions=1)

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="FIRST",
        event_id="FIRST-RELEASE",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.close_expired(db_session, now=datetime(2026, 10, 4, 15, 30))
    assert first.status == "COMPLETED"

    # Global paper reservation is based on ONGOING trades only, so the same
    # 30k can be reserved again after expiry. Daily cap is 60k, so this is
    # intentionally allowed even though the first 30k remains in today's
    # cumulative daily usage.
    assert AlertService().dispatch(
        db_session, _event("AFTER-EXPIRY-CAP-RELEASE", capital=30000, lots=1)
    ) == 0

    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "AFTER-EXPIRY-CAP-RELEASE"
    assert rows[0].capital_used == 30000.0
