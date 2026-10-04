"""Alert-level regression for completed-event rollover identity."""

from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, LivePaperTrade
from app.models.global_paper_setting import GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService, _ist_day_start_utc_naive


def _event(event_id, *, capital=30000, lots=1):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol="ROLLOVER",
        timestamp_ns=1,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": lots,
                "edge": 20,
                "capital_used": capital,
            },
        },
    )


def test_completed_previous_ist_day_event_cannot_reopen_during_today_rollover(db_session, monkeypatch):
    """A consumed event stays consumed across IST midnight while new events use today's budget."""
    from datetime import timezone

    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000, max_simultaneous_positions=5, max_loss=1000,
    ))

    previous_day = datetime(2026, 10, 3, 10, 0)  # 15:30 IST
    db_session.add(LivePaperTrade(
        user_id=1,
        strategy_id="cash-future",
        symbol="ROLLOVER",
        event_id="ROLLOVER-CONSUMED",
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=1,
        entry_edge=10,
        current_edge=12,
        capital_used=30000,
        unrealized_pnl=200,
        realized_pnl=200,
        pnl_pct=round(200 / 30000 * 100, 8),
        status="COMPLETED",
        exit_reason="EXPIRY_CLOSE",
        opened_at=previous_day,
        closed_at=previous_day,
        last_mark_at=previous_day,
        legs_json="[]",
        metadata_json="{}",
    ))
    db_session.commit()

    fixed_now = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(fixed_now) == datetime(2026, 10, 3, 18, 30)

    # Freeze the dispatcher day boundary at the rollover point so this test
    # remains deterministic regardless of the machine's actual clock.
    monkeypatch.setattr(
        "app.notifications.common._ist_day_start_utc_naive",
        lambda now=None: datetime(2026, 10, 3, 18, 30),
    )

    # Replaying the consumed previous-day event must not reopen or mutate it,
    # even though today's risk windows are now empty.
    assert AlertService().dispatch(
        db_session, _event("ROLLOVER-CONSUMED")
    ) == 0

    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"
    assert rows[0].event_id == "ROLLOVER-CONSUMED"
    assert rows[0].current_edge == 12
    assert rows[0].realized_pnl == 200

    # A genuinely new event after IST rollover is allowed to consume today's
    # fresh ₹30k daily-cap budget; the previous-day completed trade is excluded.
    assert AlertService().dispatch(
        db_session, _event("ROLLOVER-NEW")
    ) == 0

    ongoing = LivePaperTradeService().ongoing(db_session, 1)
    assert len(ongoing) == 1
    assert ongoing[0].event_id == "ROLLOVER-NEW"
    assert ongoing[0].capital_used == 30000
