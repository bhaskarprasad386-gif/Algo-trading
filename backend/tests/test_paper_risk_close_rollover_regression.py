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


def test_same_user_multi_strategy_limits_share_simultaneous_daily_and_loss_budgets(db_session):
    """Calendar/Synthetic/Box rules must consume one user's global risk budgets."""
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=90000, emergency_stop=False,
    ))
    for strategy in ("calendar-spread", "synthetic-future-cash-carry", "box-spread"):
        db_session.add(AlertRule(
            user_id=1, strategy_id=strategy, min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=1,
            max_loss=100,
        ))
    db_session.commit()

    svc = LivePaperTradeService()

    calendar_event = _event("MULTI-CAL", capital=30000, lots=1)
    calendar_event = AlertEvent(
        strategy_id="calendar-spread",
        event_id=calendar_event.event_id,
        symbol=calendar_event.symbol,
        timestamp_ns=calendar_event.timestamp_ns,
        message=calendar_event.message,
        observed_at=calendar_event.observed_at,
        metadata=calendar_event.metadata,
    )
    assert AlertService().dispatch(db_session, calendar_event) == 0
    ongoing = svc.ongoing(db_session, 1)
    assert len(ongoing) == 1
    assert ongoing[0].strategy_id == "calendar-spread"

    # The second strategy cannot create a second position for the same user:
    # max_simultaneous_positions is a user-global budget, not a per-strategy
    # bucket.
    synthetic_event = AlertEvent(
        strategy_id="synthetic-future-cash-carry",
        event_id="MULTI-SYN",
        symbol="SYN",
        timestamp_ns=2,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000,
        }},
    )
    assert AlertService().dispatch(db_session, synthetic_event) == 0
    assert len(svc.ongoing(db_session, 1)) == 1

    # Close the Calendar position at the exact max-loss boundary. Its 30k
    # allocation remains part of today's cumulative daily-capital usage.
    seed = svc.ongoing(db_session, 1)[0]
    svc.mark(db_session, seed, edge=0.0, pnl_override=-100.0)
    svc.close(db_session, seed, "MANUAL")

    # Box cannot enter even though the simultaneous slot and 30k ongoing
    # reservation were released: the same user's realized loss budget is
    # consumed globally across strategies.
    box_loss_event = AlertEvent(
        strategy_id="box-spread",
        event_id="MULTI-BOX-LOSS",
        symbol="BOX",
        timestamp_ns=3,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000,
        }},
    )
    assert AlertService().dispatch(db_session, box_loss_event) == 0
    assert len(svc.ongoing(db_session, 1)) == 0
    assert len(svc.completed(db_session, 1)) == 1
    assert svc.completed(db_session, 1)[0].realized_pnl == -100.0


def test_same_user_three_strategy_concurrent_entries_share_global_capital_and_position_limit(tmp_path):
    """Concurrent Calendar/Synthetic/Box entries must serialize one shared budget."""
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'three-strategy-paper-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=60000, emergency_stop=False,
        ))
        for strategy in ("calendar-spread", "synthetic-future-cash-carry", "box-spread"):
            setup.add(AlertRule(
                user_id=1, strategy_id=strategy, min_gross_profit=0,
                mobile_number="", whatsapp_enabled=False, enabled=True,
                max_daily_capital=60000, max_simultaneous_positions=1,
                max_loss=1000,
            ))
        setup.commit()
    finally:
        setup.close()

    events = [
        ("calendar-spread", "CONCURRENT-CALENDAR"),
        ("synthetic-future-cash-carry", "CONCURRENT-SYNTHETIC"),
        ("box-spread", "CONCURRENT-BOX"),
    ]
    barrier = threading.Barrier(3)
    errors = []
    results = []

    def worker(strategy_id, event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(
                strategy_id=strategy_id,
                event_id=event_id,
                symbol=event_id,
                timestamp_ns=1,
                message="concurrent paper",
                observed_at=datetime.utcnow(),
                metadata={"gross_profit": 1000, "paper_trade": {
                    "direction": "LONG",
                    "expiry": "2026-10-30",
                    "earliest_expiry": "2026-10-30",
                    "lot_size": 10,
                    "lots": 1,
                    "edge": 5,
                    "capital_used": 30000,
                }},
            )
            results.append((strategy_id, AlertService().dispatch(db, event)))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=item)
        for item in events
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(results) == 3

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "ONGOING"
        assert rows[0].strategy_id in {
            "calendar-spread",
            "synthetic-future-cash-carry",
            "box-spread",
        }
        assert rows[0].capital_used == 30000
        assert rows[0].lots == 1

        # The other two concurrent attempts must not leave phantom reservations
        # or duplicate positions under the shared user-global lock.
        assert sum(float(row.capital_used) for row in rows) == 30000
        assert sum(
            1 for row in rows if row.status == "ONGOING"
        ) == 1
    finally:
        verify.close()
        engine.dispose()


def test_same_user_three_strategy_concurrent_two_lot_requests_allocate_only_global_capital(tmp_path):
    """Concurrent 2-lot requests must consume the shared 60k budget exactly once."""
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'three-strategy-partial-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=60000, emergency_stop=False,
        ))
        for strategy in ("calendar-spread", "synthetic-future-cash-carry", "box-spread"):
            setup.add(AlertRule(
                user_id=1, strategy_id=strategy, min_gross_profit=0,
                mobile_number="", whatsapp_enabled=False, enabled=True,
                max_daily_capital=60000, max_simultaneous_positions=3,
                max_loss=1000,
            ))
        setup.commit()
    finally:
        setup.close()

    events = [
        ("calendar-spread", "PARTIAL-CALENDAR"),
        ("synthetic-future-cash-carry", "PARTIAL-SYNTHETIC"),
        ("box-spread", "PARTIAL-BOX"),
    ]
    barrier = threading.Barrier(3)
    errors = []
    results = []

    def worker(strategy_id, event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(
                strategy_id=strategy_id,
                event_id=event_id,
                symbol=event_id,
                timestamp_ns=1,
                message="concurrent partial paper",
                observed_at=datetime.utcnow(),
                metadata={"gross_profit": 1000, "paper_trade": {
                    "direction": "LONG",
                    "expiry": "2026-10-30",
                    "earliest_expiry": "2026-10-30",
                    "lot_size": 10,
                    "lots": 2,
                    "edge": 5,
                    "capital_used": 60000,
                }},
            )
            results.append((strategy_id, AlertService().dispatch(db, event)))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=item)
        for item in events
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(results) == 3

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        assert 1 <= len(rows) <= 2
        assert sum(int(row.lots) for row in rows) == 2
        assert sum(float(row.capital_used) for row in rows) == 60000
        assert all(int(row.lots) >= 1 for row in rows)
        assert all(float(row.capital_used) == 30000 for row in rows)

        # Two 30k lots are globally allocatable; the third concurrent request
        # must fail without creating a zero-lot/zero-capital phantom row.
    finally:
        verify.close()
        engine.dispose()


def test_same_user_concurrent_multi_strategy_daily_boundary_and_realized_loss_stays_global(tmp_path):
    """Concurrent cross-strategy entries cannot bypass daily capital or realized loss."""
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'multi-strategy-daily-loss-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=90000, emergency_stop=False,
        ))
        for strategy in ("calendar-spread", "synthetic-future-cash-carry", "box-spread"):
            setup.add(AlertRule(
                user_id=1, strategy_id=strategy, min_gross_profit=0,
                mobile_number="", whatsapp_enabled=False, enabled=True,
                max_daily_capital=60000, max_simultaneous_positions=3,
                max_loss=100,
            ))
        setup.commit()

        seed, created = LivePaperTradeService().enter_or_mark(
            setup,
            strategy_id="calendar-spread", symbol="SEED",
            event_id="DAILY-LOSS-SEED", direction="LONG",
            expiry="2026-10-30", earliest_expiry="2026-10-30",
            lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
        )
        assert created is True
        LivePaperTradeService().mark(setup, seed, edge=0.0, pnl_override=-100.0)
        LivePaperTradeService().close(setup, seed, "MANUAL")
        setup.commit()
    finally:
        setup.close()

    events = [
        ("synthetic-future-cash-carry", "DAILY-LOSS-SYN"),
        ("box-spread", "DAILY-LOSS-BOX"),
        ("calendar-spread", "DAILY-LOSS-CAL2"),
    ]
    barrier = threading.Barrier(3)
    errors = []
    results = []

    def worker(strategy_id, event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(
                strategy_id=strategy_id,
                event_id=event_id,
                symbol=event_id,
                timestamp_ns=10,
                message="daily/loss boundary",
                observed_at=datetime.utcnow(),
                metadata={"gross_profit": 1000, "paper_trade": {
                    "direction": "LONG",
                    "expiry": "2026-10-30",
                    "earliest_expiry": "2026-10-30",
                    "lot_size": 10,
                    "lots": 1,
                    "edge": 5,
                    "capital_used": 30000,
                }},
            )
            results.append((strategy_id, AlertService().dispatch(db, event)))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=item) for item in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(results) == 3

    verify = Session()
    try:
        completed = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "COMPLETED",
        ).all()
        ongoing = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()

        # The seed consumed 30k of today's 60k budget and exhausted the
        # user's max-loss budget. No concurrent strategy may create another
        # position, even though 60k of paper capital is still technically free.
        assert len(completed) == 1
        assert completed[0].realized_pnl == -100.0
        assert ongoing == []
    finally:
        verify.close()
        engine.dispose()


def test_partial_allocation_uses_actual_capital_and_max_loss_rejection_leaves_no_reservation(tmp_path):
    """Partial capital and max-loss gates must compose without phantom reservations."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'partial-loss-composition.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=60000, emergency_stop=False,
        ))
        setup.add(AlertRule(
            user_id=1, strategy_id="calendar-spread", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=3,
            max_loss=100,
        ))
        setup.commit()

        seed, created = LivePaperTradeService().enter_or_mark(
            setup,
            strategy_id="calendar-spread", symbol="LOSS-SEED",
            event_id="LOSS-SEED", direction="LONG",
            expiry="2026-10-30", earliest_expiry="2026-10-30",
            lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
        )
        assert created is True
        LivePaperTradeService().mark(setup, seed, edge=0.0, pnl_override=-50.0)
        setup.commit()

        # Only 30k remains. A 2-lot/60k request must be reduced to one
        # 30k lot before the risk accounting is persisted.
        event = AlertEvent(
            strategy_id="calendar-spread",
            event_id="PARTIAL-AFTER-LOSS",
            symbol="PARTIAL-AFTER-LOSS",
            timestamp_ns=1,
            message="partial after open loss",
            observed_at=datetime.utcnow(),
            metadata={"gross_profit": 1000, "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 2,
                "edge": 5,
                "capital_used": 60000,
            }},
        )
        result = AlertService().dispatch(setup, event)
        assert result == 1
        rows = setup.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
        assert len(rows) == 2
        assert sum(int(row.lots) for row in rows) == 2
        assert sum(float(row.capital_used) for row in rows) == 60000
        assert all(float(row.capital_used) == 30000 for row in rows)

        # Now the open loss reaches the exact max-loss boundary. A fresh
        # request must be rejected and must not reserve any additional capital.
        LivePaperTradeService().mark(setup, seed, edge=0.0, pnl_override=-100.0)
        setup.commit()
        rejected = AlertEvent(
            strategy_id="calendar-spread",
            event_id="MAX-LOSS-BLOCK",
            symbol="MAX-LOSS-BLOCK",
            timestamp_ns=2,
            message="max loss boundary",
            observed_at=datetime.utcnow(),
            metadata={"gross_profit": 1000, "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 30000,
            }},
        )
        assert AlertService().dispatch(setup, rejected) == 0
        final_rows = setup.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        assert len(final_rows) == 2
        assert sum(float(row.capital_used) for row in final_rows) == 60000
    finally:
        setup.close()
        engine.dispose()
