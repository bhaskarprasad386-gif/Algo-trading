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


def test_same_user_daily_capital_is_shared_across_strategy_rules(db_session):
    """Daily capital is user-wide when the user has limits on multiple strategies."""
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=90000, emergency_stop=False,
    ))
    for strategy in ("calendar-spread", "synthetic-future-cash-carry", "box-spread"):
        db_session.add(AlertRule(
            user_id=1, strategy_id=strategy, min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=5, max_loss=0,
        ))
    db_session.commit()

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="CAL",
        event_id="DAILY-CROSS-CAL", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    svc.close(db_session, first, "MANUAL")

    # The next strategy cannot treat its own strategy bucket as fresh capital:
    # the same user's completed Calendar allocation already consumed today's
    # 30k of the shared 60k daily budget.
    event = AlertEvent(
        strategy_id="synthetic-future-cash-carry",
        event_id="DAILY-CROSS-SYN",
        symbol="SYN",
        timestamp_ns=2,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000,
        }},
    )
    assert AlertService().dispatch(db_session, event) == 0

    # A second 30k allocation exactly reaches the shared daily boundary.
    event2 = AlertEvent(
        strategy_id="box-spread",
        event_id="DAILY-CROSS-BOX",
        symbol="BOX",
        timestamp_ns=3,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000,
        }},
    )
    assert AlertService().dispatch(db_session, event2) == 0
    rows = svc.ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].strategy_id == "box-spread"
    assert rows[0].capital_used == 30000


def test_partial_allocation_that_exceeds_remaining_daily_budget_leaves_no_phantom_reservation(db_session):
    """Partial global allocation is still rejected when the remaining daily budget is smaller."""
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=50000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="calendar-spread", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=50000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    seed, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="SEED",
        event_id="DAILY-PARTIAL-SEED", direction="LONG",
        expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    svc.close(db_session, seed, "MANUAL")

    # Global paper amount has 50k available, so a 60k/2-lot request computes
    # one effective 30k lot. But only 20k remains in today's daily budget.
    event = AlertEvent(
        strategy_id="calendar-spread",
        event_id="DAILY-PARTIAL-REJECT",
        symbol="REJECT",
        timestamp_ns=4,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "lot_size": 10, "lots": 2, "edge": 5, "capital_used": 60000,
        }},
    )
    assert AlertService().dispatch(db_session, event) == 0

    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).all()
    assert len(rows) == 1
    assert rows[0].event_id == "DAILY-PARTIAL-SEED"
    assert rows[0].status == "COMPLETED"
    assert rows[0].capital_used == 30000


def test_expiry_reuse_consumes_new_daily_capital_but_not_double_reserved_capital(db_session):
    """After expiry, reused global capital is cumulative in daily usage exactly once per trade."""
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=60000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="EXPIRY-A",
        event_id="DAILY-EXPIRY-A", direction="LONG",
        expiry="2026-10-04", earliest_expiry="2026-10-04",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    svc.close_expired(db_session, now=datetime(2026, 10, 4, 15, 30))

    event = _event("DAILY-EXPIRY-B", capital=30000, lots=1)
    assert AlertService().dispatch(db_session, event) == 0

    ongoing = svc.ongoing(db_session, 1)
    completed = svc.completed(db_session, 1)
    assert len(ongoing) == 1
    assert len(completed) == 1
    assert sum(float(row.capital_used) for row in completed + ongoing) == 60000
    assert sum(float(row.capital_used) for row in ongoing) == 30000
