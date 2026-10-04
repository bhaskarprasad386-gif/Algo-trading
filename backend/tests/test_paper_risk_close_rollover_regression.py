"""Regression coverage for paper-risk accounting across close and IST rollover."""

from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule
from app.models.global_paper_setting import GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, capital=30000, lots=1):
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
                "lots": lots,
                "edge": 5,
                "capital_used": capital,
            },
        },
    )


def test_completed_trade_releases_reserved_capital_but_still_counts_daily_capital(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=50000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=50000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="FIRST",
        event_id="ROLLOVER-FIRST", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    assert first.capital_used == 30000

    svc.mark(db_session, first, edge=2)
    first = svc.close(db_session, first, "MANUAL")
    assert first.status == "COMPLETED"
    assert first.realized_pnl == -30

    # Closing releases the reserved global capital, but daily-capital accounting
    # deliberately retains the opening-day ₹30k.
    second_event = _event("ROLLOVER-SECOND")
    assert AlertService().dispatch(db_session, second_event) == 0
    assert svc.ongoing(db_session, 1) == []


def test_next_ist_day_allows_new_daily_capital_after_previous_day_close(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=50000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000, max_simultaneous_positions=5, max_loss=100,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="DAY1",
        event_id="DAY1-TRADE", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    svc.mark(db_session, trade, edge=0, pnl_override=-100)
    trade = svc.close(db_session, trade, "MANUAL")
    assert trade.realized_pnl == -100

    # Move the persisted timestamps to just before the IST day boundary.
    trade.opened_at = datetime(2026, 10, 4, 18, 29, 59)
    trade.closed_at = datetime(2026, 10, 4, 18, 29, 59)
    db_session.commit()

    # At exactly 00:00 IST, the old opening-day capital no longer counts.
    now = datetime(2026, 10, 4, 18, 30, tzinfo=timezone.utc)
    with __import__("unittest").mock.patch(
        "app.notifications.common._ist_day_start_utc_naive",
        return_value=datetime(2026, 10, 4, 18, 30),
    ):
        result = AlertService().dispatch(db_session, _event("DAY2-TRADE"))

    assert result == 0
    rows = svc.ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "DAY2-TRADE"
