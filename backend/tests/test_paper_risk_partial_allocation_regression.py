"""Regression coverage for risk gates with partially allocated paper capital."""
from datetime import datetime

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule
from app.models.global_paper_setting import GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, capital=60000, lots=2):
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


def test_partial_allocation_uses_actual_capital_for_daily_capital_gate(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=50000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=50000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    # Requested ₹60k / 2 lots, but only ₹50k is globally available, so the
    # actual allocation is one ₹30k lot. The daily-cap gate must evaluate the
    # same effective ₹30k, not the requested ₹60k.
    assert AlertService().dispatch(db_session, _event("PARTIAL-DAILY-1")) == 0
    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].lots == 1
    assert rows[0].capital_used == 30000

    # Remaining global capital is ₹20k, so the second request can only
    # allocate zero lots and must not create an oversubscribed position.
    assert AlertService().dispatch(db_session, _event("PARTIAL-DAILY-2")) == 0
    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert sum(float(row.capital_used) for row in rows) == 30000


def test_partial_allocation_does_not_weaken_max_loss_gate(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=50000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0, max_simultaneous_positions=5, max_loss=100,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    seed, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="SEED",
        event_id="PARTIAL-LOSS-SEED", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=2, edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    assert seed.lots == 1
    assert seed.capital_used == 30000

    svc.mark(db_session, seed, edge=0, pnl_override=-100.0)
    assert seed.unrealized_pnl == -100.0

    # The partial allocation does not reduce the realized/open loss below the
    # configured ₹100 max-loss threshold.
    assert AlertService().dispatch(db_session, _event("PARTIAL-LOSS-BLOCK")) == 0
    rows = svc.ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "PARTIAL-LOSS-SEED"


def test_malformed_persisted_trade_fails_closed_before_new_paper_entry(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=100_000, max_simultaneous_positions=5, max_loss=1_000,
    ))
    from app.models import LivePaperTrade
    db_session.add(LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="BAD",
        event_id="BAD-RISK-ROW", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        entry_edge=10, current_edge=10, capital_used=float("inf"),
        unrealized_pnl=-10, realized_pnl=0, pnl_pct=0,
        status="ONGOING",
    ))
    db_session.commit()

    assert AlertService().dispatch(db_session, _event("BLOCKED-BY-CORRUPTION")) == 0
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "BLOCKED-BY-CORRUPTION",
    ).all()
    assert rows == []


def test_concurrent_different_events_respect_max_daily_capital(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'daily-cap-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=200000, emergency_stop=False))
    setup.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=60000, max_simultaneous_positions=5, max_loss=0,
    ))
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker(event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            results.append(AlertService().dispatch(db, _event(event_id, capital=60000, lots=1)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=("DAILY-RACE-A",)),
        threading.Thread(target=worker, args=("DAILY-RACE-B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2

    verify = Session()
    try:
        rows = LivePaperTradeService().ongoing(verify, 1)
        assert len(rows) == 1
        assert rows[0].capital_used == 60000
        assert rows[0].event_id in {"DAILY-RACE-A", "DAILY-RACE-B"}
    finally:
        verify.close()
        engine.dispose()


def test_concurrent_different_events_respect_max_simultaneous_positions(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'position-cap-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=200000, emergency_stop=False))
    setup.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0, max_simultaneous_positions=1, max_loss=0,
    ))
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    errors = []
    start_results = []

    def worker(event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            AlertService().dispatch(db, _event(event_id, capital=60000, lots=1))
            start_results.append(event_id)
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=("POSITION-RACE-A",)),
        threading.Thread(target=worker, args=("POSITION-RACE-B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []

    verify = Session()
    try:
        rows = LivePaperTradeService().ongoing(verify, 1)
        assert len(rows) == 1
        assert rows[0].event_id in {"POSITION-RACE-A", "POSITION-RACE-B"}
    finally:
        verify.close()
        engine.dispose()


def test_failed_risk_locked_entry_rolls_back_before_next_alert(db_session, monkeypatch):
    from app.models import AlertRule

    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=100000, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    original = LivePaperTradeService.enter_or_mark
    calls = {"count": 0}

    def fail_once(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("forced insert failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LivePaperTradeService, "enter_or_mark", fail_once)

    first = _event("ROLLBACK-FIRST", capital=60000, lots=1)
    assert AlertService().dispatch(db_session, first) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0

    # The failed entry happened after the per-user risk lock was acquired.
    # The dispatcher must roll back that transaction so the next alert can
    # acquire the same lock and reserve capital normally.
    second = _event("ROLLBACK-SECOND", capital=60000, lots=1)
    assert AlertService().dispatch(db_session, second) == 0

    rows = LivePaperTradeService().ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "ROLLBACK-SECOND"
    assert rows[0].capital_used == 60000
