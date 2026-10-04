"""Regression coverage for combined open/realized max-loss accounting."""

from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db, *, max_loss=100.0, paper_amount=200000.0):
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
        max_daily_capital=0.0,
        max_simultaneous_positions=5,
        max_loss=max_loss,
    ))
    db.commit()


def _event(event_id, *, capital=30000.0):
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
                "lots": 1,
                "edge": 5,
                "capital_used": capital,
            },
        },
    )


def _completed_trade(db, event_id, realized_pnl, *, closed_at=None):
    when = closed_at or datetime(2026, 10, 4, 10, 0)
    trade = LivePaperTrade(
        user_id=1,
        strategy_id="cash-future",
        symbol=event_id,
        event_id=event_id,
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=1,
        entry_edge=10,
        current_edge=9,
        capital_used=30000,
        unrealized_pnl=realized_pnl,
        realized_pnl=realized_pnl,
        pnl_pct=round(realized_pnl / 30000 * 100, 8),
        status="COMPLETED",
        exit_reason="MANUAL",
        opened_at=when,
        closed_at=when,
        last_mark_at=when,
        legs_json="[]",
        metadata_json="{}",
    )
    db.add(trade)
    return trade


def test_max_loss_combines_open_and_same_day_realized_losses_at_exact_boundary(db_session):
    _setup(db_session, max_loss=100.0)

    _completed_trade(db_session, "REALIZED-LOSS", -50.0)
    db_session.commit()

    svc = LivePaperTradeService()
    open_trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="OPEN-LOSS",
        event_id="OPEN-LOSS",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, open_trade, edge=5.0)
    db_session.commit()
    assert open_trade.unrealized_pnl == -50.0

    # Same-day realized -50 plus ongoing -50 reaches exactly -max_loss.
    assert AlertService().dispatch(
        db_session, _event("COMBINED-EXACT")
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "COMBINED-EXACT",
    ).count() == 0


def test_positive_realized_pnl_cannot_offset_combined_open_and_realized_loss(db_session):
    _setup(db_session, max_loss=100.0)

    _completed_trade(db_session, "WIN", 100.0)
    _completed_trade(db_session, "LOSS", -60.0)
    db_session.commit()

    svc = LivePaperTradeService()
    open_trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="OPEN-LOSS",
        event_id="OPEN-LOSS-POSITIVE-OFFSET",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, open_trade, edge=6.0)
    db_session.commit()
    assert open_trade.unrealized_pnl == -40.0

    # +100 must not offset -60 realized + -40 open. Effective loss is -100.
    assert AlertService().dispatch(
        db_session, _event("POSITIVE-OFFSET-BLOCKED")
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "POSITIVE-OFFSET-BLOCKED",
    ).count() == 0


def test_expiry_close_moves_open_loss_into_same_day_realized_loss_before_gate(db_session):
    _setup(db_session, max_loss=100.0)

    svc = LivePaperTradeService()
    expired, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="EXPIRING-LOSS",
        event_id="EXPIRING-LOSS",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, expired, edge=5.0)
    db_session.commit()
    assert expired.unrealized_pnl == -50.0

    # A new alert after NSE close must first expire the old trade. Its -50
    # then becomes today's realized loss and still participates in max_loss.
    assert AlertService().dispatch(
        db_session, _event("AFTER-EXPIRY-LOSS", capital=30000)
    ) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "EXPIRING-LOSS",
    ).one()
    assert row.status == "COMPLETED"
    assert row.exit_reason == "EXPIRY_CLOSE"
    assert row.realized_pnl == -50.0

    new_row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "AFTER-EXPIRY-LOSS",
    ).one()
    assert new_row.status == "ONGOING"


def test_expiry_loss_plus_existing_realized_loss_blocks_new_entry(db_session):
    _setup(db_session, max_loss=100.0)

    _completed_trade(db_session, "REALIZED-50", -50.0)
    db_session.commit()

    svc = LivePaperTradeService()
    expired, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="EXPIRING-LOSS",
        event_id="EXPIRING-LOSS-COMBined",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, expired, edge=5.0)
    db_session.commit()

    # Expiry close realizes -50; existing same-day realized loss is -50.
    # The new event must see the combined exact -100 boundary and block.
    assert AlertService().dispatch(
        db_session, _event("EXPIRY-COMBINED-BLOCKED")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "EXPIRY-COMBINED-BLOCKED",
    ).count() == 0

    expired_row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "EXPIRING-LOSS-COMBined",
    ).one()
    assert expired_row.status == "COMPLETED"
    assert expired_row.realized_pnl == -50.0


def test_manual_close_loss_contributes_to_same_day_max_loss_for_next_entry(db_session):
    _setup(db_session, max_loss=100.0)

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="MANUAL-LOSS",
        event_id="MANUAL-LOSS",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, first, edge=5.0)
    svc.close(db_session, first, "MANUAL")
    assert first.status == "COMPLETED"
    assert first.realized_pnl == -50.0

    second, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="OPEN-LOSS",
        event_id="OPEN-LOSS-MANUAL-COMBINE",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, second, edge=5.0)
    db_session.commit()
    assert second.unrealized_pnl == -50.0

    # Manual realized -50 + ongoing -50 reaches the exact stop.
    assert AlertService().dispatch(
        db_session, _event("MANUAL-COMBINED-BLOCKED")
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "MANUAL-COMBINED-BLOCKED",
    ).count() == 0
