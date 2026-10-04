"""Regression coverage for atomic dispatcher allocation and fail-closed rollback."""

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auto.live_paper import LivePaperTradeService
from app.core.database import Base
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, *, capital=60000.0, lots=2, edge=5.0):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="atomic-allocation",
        metadata={
            "gross_profit": 0,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": lots,
                "edge": edge,
                "capital_used": capital,
            },
        },
    )


def test_fail_closed_enter_or_mark_rolls_back_dispatch_risk_lock(db_session, monkeypatch):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    original = LivePaperTradeService.enter_or_mark

    def fail_closed(self, *args, **kwargs):
        return None, False

    monkeypatch.setattr(LivePaperTradeService, "enter_or_mark", fail_closed)

    assert AlertService().dispatch(
        db_session, _event("FAIL-CLOSED-ROLLBACK")
    ) == 0

    # The dispatcher acquired the per-user GlobalPaperSetting write lock before
    # calling enter_or_mark(). A fail-closed (None, False) result is not an
    # exception, so the dispatcher must explicitly rollback that transaction.
    assert not db_session.in_transaction()
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0

    monkeypatch.setattr(LivePaperTradeService, "enter_or_mark", original)

    # The same session must immediately be able to reserve the full budget;
    # there must be no phantom reservation or dirty transaction from the
    # rejected attempt.
    assert AlertService().dispatch(
        db_session, _event("FAIL-CLOSED-RECOVERY", capital=30000, lots=1)
    ) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "FAIL-CLOSED-RECOVERY",
    ).one()
    assert row.lots == 1
    assert row.capital_used == 30000


def test_partial_allocation_remains_exact_after_fail_closed_downstream_path(db_session, monkeypatch):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    original = LivePaperTradeService.enter_or_mark
    calls = {"count": 0}

    def fail_once(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            # Simulate downstream validation returning fail-closed instead of
            # raising after the dispatcher already calculated one effective lot.
            return None, False
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LivePaperTradeService, "enter_or_mark", fail_once)

    assert AlertService().dispatch(
        db_session, _event("PARTIAL-FAIL-THEN-RECOVER")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0
    assert not db_session.in_transaction()

    # Original request is 2 lots / 60k, but only 30k is available. After the
    # failed attempt, the same exact one-lot allocation must remain available.
    assert AlertService().dispatch(
        db_session, _event("PARTIAL-RECOVERED")
    ) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "PARTIAL-RECOVERED",
    ).one()
    assert row.lots == 1
    assert row.capital_used == 30000


def test_concurrent_partial_entries_respect_exact_max_loss_boundary(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'atomic-max-loss-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=90000, emergency_stop=False,
        ))
        setup.add(AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=5,
            max_loss=100,
        ))
        setup.commit()

        seed_service = LivePaperTradeService()
        seed, created = seed_service.enter_or_mark(
            setup,
            strategy_id="cash-future",
            symbol="LOSS-SEED",
            event_id="LOSS-SEED",
            direction="LONG",
            expiry="2026-10-30",
            earliest_expiry="2026-10-30",
            lot_size=10,
            lots=2,
            edge=10,
            capital_used=60000,
            user_id=1,
        )
        assert created is True
        assert seed.lots == 2
        seed_service.mark(setup, seed, edge=9, pnl_override=-100)
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    errors = []
    results = []

    def worker(event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            results.append(AlertService().dispatch(
                db, _event(event_id, capital=60000, lots=2)
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=("MAX-LOSS-RACE-A",)),
        threading.Thread(target=worker, args=("MAX-LOSS-RACE-B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
        assert len(rows) == 1
        assert rows[0].event_id == "LOSS-SEED"
        assert rows[0].capital_used == 60000
        assert rows[0].unrealized_pnl == -100

        # Exact max-loss boundary is a hard stop. Neither concurrent request
        # may create even a one-lot partial allocation.
        assert verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id.in_(
                ["MAX-LOSS-RACE-A", "MAX-LOSS-RACE-B"]
            ),
        ).count() == 0
    finally:
        verify.close()
        engine.dispose()


def test_dispatch_expiry_does_not_commit_caller_pending_work(db_session):
    """Alert expiry cleanup must not commit unrelated caller-owned mutations."""
    from datetime import datetime
    from app.models import GlobalPaperSetting
    from app.auto.live_paper import LivePaperTradeService

    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=100,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=60000, max_simultaneous_positions=1, max_loss=100,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future", symbol="EXPIRING",
        event_id="DISPATCH-TX-EXPIRY",
        direction="LONG", expiry="2026-10-04",
        earliest_expiry="2026-10-04", lot_size=10, lots=1,
        edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    db_session.commit()

    setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    setting.paper_amount = 45000

    # dispatch() invokes expiry cleanup through an independent session. The
    # caller's 45k mutation must remain uncommitted and therefore be removable
    # by this caller's rollback.
    event = AlertEvent(
        strategy_id="cash-future",
        event_id="DISPATCH-TX-NEW",
        symbol="NEW",
        timestamp_ns=9,
        message="tx-boundary",
        observed_at=datetime.utcnow(),
        metadata={
            "gross_profit": 0,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 30000,
            },
        },
    )
    with db_session.no_autoflush:
        assert AlertService().dispatch(
            db_session, event
        ) == 0

    # The expiry itself is durable, but the caller-owned setting mutation is
    # not committed by dispatch.
    db_session.rollback()
    verify_trade = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == trade.id
    ).one()
    verify_setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    assert verify_trade.status == "COMPLETED"
    assert verify_trade.exit_reason == "EXPIRY_CLOSE"
    assert verify_setting.paper_amount == 30000


def test_expiry_cleanup_database_lock_fails_closed_without_leaking_caller_transaction(db_session, monkeypatch):
    """A locked independent expiry transaction must reject the alert safely."""
    from sqlalchemy.exc import OperationalError
    import app.notifications.common as common_module

    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000, max_simultaneous_positions=1, max_loss=0,
    ))
    db_session.commit()

    class LockedExpirySession:
        def rollback(self):
            pass

        def close(self):
            pass

        def close_expired(self, *args, **kwargs):
            raise OperationalError(
                "UPDATE live_paper_trade SET status=?",
                {},
                Exception("database is locked"),
            )

    # The real SessionLocal is patched only for the short-lived expiry session.
    # The production dispatcher must fail closed rather than evaluate risk gates
    # against a potentially stale ongoing reservation/loss snapshot.
    monkeypatch.setattr(common_module, "SessionLocal", lambda: LockedExpirySession())

    event = _event("EXPIRY-DB-LOCK")
    assert AlertService().dispatch(db_session, event) == 0
    assert not db_session.in_transaction()
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "EXPIRY-DB-LOCK",
    ).count() == 0


def test_concurrent_expiry_cleanup_does_not_duplicate_close_or_entry(tmp_path):
    """Two alert sessions at the expiry boundary must converge without duplicate paper rows."""
    from datetime import datetime
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'expiry-dispatch-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
        ))
        setup.add(AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=1, max_loss=100,
        ))
        setup.commit()
        seed_service = LivePaperTradeService()
        seed, created = seed_service.enter_or_mark(
            setup, strategy_id="cash-future", symbol="EXPIRING-RACE",
            event_id="EXPIRING-RACE-SEED", direction="LONG",
            expiry="2026-10-04", earliest_expiry="2026-10-04",
            lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
        )
        assert created is True
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    errors = []
    results = []

    def worker(event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            # Freeze expiry time at the exact NSE boundary so both independent
            # expiry sessions attempt the same terminal transition.
            original = LivePaperTradeService.close_expired
            def close_at_boundary(self, session, **kwargs):
                return original(
                    self, session, now=datetime(2026, 10, 4, 15, 30),
                    **{k: v for k, v in kwargs.items() if k != "now"},
                )
            # The service method is patched per instance to keep this test
            # deterministic without changing production clock behavior.
            service = AlertService()
            from unittest.mock import patch
            with patch.object(
                LivePaperTradeService, "close_expired", close_at_boundary
            ):
                results.append(service.dispatch(
                    db, _event(event_id)
                ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=("EXPIRY-CONCURRENT-A",)),
        threading.Thread(target=worker, args=("EXPIRY-CONCURRENT-B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert len(results) == 2

    verify = Session()
    try:
        completed = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "EXPIRING-RACE-SEED",
            LivePaperTrade.status == "COMPLETED",
        ).all()
        ongoing = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
        assert len(completed) == 1
        assert len(ongoing) == 1
        assert ongoing[0].capital_used == 30000
        assert ongoing[0].event_id in {
            "EXPIRY-CONCURRENT-A", "EXPIRY-CONCURRENT-B"
        }
    finally:
        verify.close()
        engine.dispose()
