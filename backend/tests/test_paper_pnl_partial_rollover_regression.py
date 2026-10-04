"""Combined regression coverage for partial paper P&L, terminal close, and IST rollover."""
from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def test_partial_pnl_pct_survives_expiry_and_daily_capital_uses_allocated_amount(db_session):
    svc = LivePaperTradeService()
    db_session.add(
        GlobalPaperSetting(
            user_id=1,
            enabled=True,
            paper_amount=50000,
            emergency_stop=False,
        )
    )
    db_session.commit()

    # Request 2 lots / ₹60k against a ₹50k global cap: only one lot / ₹30k
    # is actually allocated and that persisted amount is the P&L denominator.
    trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="ROLLOVER",
        event_id="PARTIAL-ROLLOVER-PNL",
        direction="LONG",
        expiry="2026-10-12",
        earliest_expiry="2026-10-12",
        lot_size=10,
        lots=2,
        edge=10,
        capital_used=60000,
        metadata={"exchange": "NFO"},
        user_id=1,
    )
    assert created is True
    assert trade.lots == 1
    assert trade.capital_used == 30000

    # -₹100 loss on the allocated lot = -0.33333333% of actual capital.
    svc.mark(db_session, trade, edge=0, pnl_override=-100.0)
    assert trade.unrealized_pnl == -100.0
    assert trade.pnl_pct == round(-100.0 / 30000.0 * 100.0, 8)

    # Put the opening timestamp before IST midnight and close after it. The
    # trade's daily capital belongs to its opening IST day, while realized P&L
    # belongs to the closing IST day.
    trade.opened_at = datetime(2026, 10, 3, 18, 29, 59)
    db_session.commit()

    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 12, 15, 30, tzinfo=timezone.utc),
    )
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"
    assert closed[0].exit_reason == "EXPIRY_CLOSE"
    assert closed[0].realized_pnl == -100.0
    assert closed[0].unrealized_pnl == -100.0
    assert closed[0].pnl_pct == round(-100.0 / 30000.0 * 100.0, 8)
    assert closed[0].capital_used == 30000

    # A capital-risk day beginning at 2026-10-04 00:00 IST (2026-10-03
    # 18:30 UTC) must not count this trade because it opened one second before
    # that boundary.
    day_start = datetime(2026, 10, 3, 18, 30)
    daily_capital = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.capital_used).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.opened_at >= day_start,
        ).all()
    )
    assert daily_capital == 0.0

    # The same trade is a realized result on its closing day, because the
    # closing timestamp is after the IST rollover.
    today_loss = sum(
        min(0.0, float(row[0] or 0.0))
        for row in db_session.query(LivePaperTrade.realized_pnl).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "COMPLETED",
            LivePaperTrade.closed_at >= day_start,
        ).all()
    )
    assert today_loss == -100.0
