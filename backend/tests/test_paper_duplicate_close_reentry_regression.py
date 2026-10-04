from datetime import datetime

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService
from app.auto.live_paper import LivePaperTradeService


def _setup(db, *, paper_amount=50_000, max_daily_capital=0, max_loss=0):
    db.add(GlobalPaperSetting(
        user_id=1,
        enabled=True,
        paper_amount=paper_amount,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=1,
        strategy_id="cash-future",
        min_gross_profit=0,
        mobile_number="",
        whatsapp_enabled=False,
        enabled=True,
        max_daily_capital=max_daily_capital,
        max_simultaneous_positions=5,
        max_loss=max_loss,
    ))
    db.commit()


def _event(event_id, *, capital=30_000, edge=5):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": edge,
                "capital_used": capital,
            },
        },
    )


def _reserved(db):
    return sum(
        float(row[0] or 0.0)
        for row in db.query(LivePaperTrade.capital_used).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    )


def test_duplicate_after_risk_rejection_does_not_allocate_or_create_phantom_trade(db_session):
    _setup(db_session, paper_amount=50_000, max_daily_capital=30_000)

    service = AlertService()
    assert service.dispatch(db_session, _event("FIRST", edge=5)) == 0
    first = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "FIRST"
    ).one()
    assert _reserved(db_session) == 30_000

    # This event is rejected by the daily-cap gate.
    assert service.dispatch(db_session, _event("BLOCKED", edge=4)) == 0
    assert _reserved(db_session) == 30_000
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "BLOCKED"
    ).count() == 0

    # Repeating the already-open event must be mark-only and must not
    # consume the capital that was released nowhere (or create another row).
    assert service.dispatch(db_session, _event("FIRST", capital=50_000, edge=7)) == 0
    db_session.refresh(first)
    assert first.status == "ONGOING"
    assert first.current_edge == 7
    assert first.capital_used == 30_000
    assert _reserved(db_session) == 30_000
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "FIRST"
    ).count() == 1


def test_close_then_new_entry_reuses_released_reservation_without_reopening_old_event(db_session):
    _setup(db_session, paper_amount=50_000, max_daily_capital=0)

    service = AlertService()
    assert service.dispatch(db_session, _event("OLD", capital=30_000, edge=5)) == 0
    old = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "OLD"
    ).one()
    assert _reserved(db_session) == 30_000

    LivePaperTradeService().mark(db_session, old, edge=4)
    closed = LivePaperTradeService().close(db_session, old, "MANUAL")
    assert closed.status == "COMPLETED"
    assert _reserved(db_session) == 0

    # A fresh event can reuse the released paper capital.
    assert service.dispatch(db_session, _event("NEW", capital=50_000, edge=6)) == 0
    new = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "NEW"
    ).one()
    assert new.status == "ONGOING"
    assert new.capital_used == 50_000
    assert _reserved(db_session) == 50_000

    # The completed event is consumed and must never reopen or reallocate.
    assert service.dispatch(db_session, _event("OLD", capital=50_000, edge=9)) == 0
    db_session.refresh(old)
    db_session.refresh(new)
    assert old.status == "COMPLETED"
    assert old.capital_used == 30_000
    assert new.status == "ONGOING"
    assert new.capital_used == 50_000
    assert _reserved(db_session) == 50_000
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "OLD"
    ).count() == 1
