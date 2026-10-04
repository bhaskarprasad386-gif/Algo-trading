"""Regression coverage for dispatcher partial allocation into LivePaperTradeService."""

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _event(event_id):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="partial",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 2,
                "edge": 5,
                "capital_used": 60000.0,
            },
        },
    )


def test_dispatch_persists_partial_lots_not_original_requested_lots(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000.0, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0.0, max_simultaneous_positions=5, max_loss=0.0,
    ))
    db_session.commit()

    assert AlertService().dispatch(db_session, _event("PARTIAL-DISPATCH")) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "PARTIAL-DISPATCH",
    ).one()
    assert row.lots == 1
    assert row.capital_used == 30000.0
