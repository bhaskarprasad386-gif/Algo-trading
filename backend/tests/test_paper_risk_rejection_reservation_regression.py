from datetime import datetime

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db, *, max_daily_capital=0, max_loss=0, paper_amount=50_000):
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


def test_rejected_risk_entry_leaves_no_capital_reservation_or_phantom_trade(db_session):
    _setup(db_session, max_daily_capital=30_000)

    service = AlertService()
    assert service.dispatch(db_session, _event("FIRST")) == 0
    assert _reserved(db_session) == 30_000

    # Same-day daily-cap rejection must not reserve anything and must not
    # mutate the existing position.
    assert service.dispatch(db_session, _event("BLOCKED")) == 0
    assert _reserved(db_session) == 30_000

    trades = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1
    ).order_by(LivePaperTrade.id.asc()).all()
    assert [trade.event_id for trade in trades] == ["FIRST"]
    assert [trade.status for trade in trades] == ["ONGOING"]


def test_max_loss_rejection_does_not_consume_available_capital(db_session):
    _setup(db_session, max_loss=10, paper_amount=50_000)

    service = AlertService()
    assert service.dispatch(db_session, _event("LOSS-SEED")) == 0
    seed = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "LOSS-SEED"
    ).one()

    # Force a persisted loss that exactly reaches the configured max loss.
    from app.auto.live_paper import LivePaperTradeService
    LivePaperTradeService().mark(db_session, seed, edge=4)

    assert service.dispatch(db_session, _event("LOSS-BLOCKED")) == 0
    assert _reserved(db_session) == 30_000

    events = {
        trade.event_id: trade.status
        for trade in db_session.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1
        ).all()
    }
    assert events == {"LOSS-SEED": "ONGOING"}
