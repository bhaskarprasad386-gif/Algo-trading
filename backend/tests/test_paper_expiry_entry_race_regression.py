"""Regression coverage for expiry-release and new-entry serialization."""

from datetime import datetime

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db):
    db.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=60000, max_simultaneous_positions=1,
        max_loss=100,
    ))
    db.commit()


def _event(event_id="AFTER-EXPIRY-RACE"):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="expiry-race",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-04",
                "earliest_expiry": "2026-10-04",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 30000,
            },
        },
    )


def _expired_trade(db):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future",
        symbol="EXPIRING",
        event_id="EXPIRING-AT-BOUNDARY",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    return trade


def test_expiry_release_is_serialized_before_new_entry_risk_read(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    svc = LivePaperTradeService()

    # This is the exact boundary where an entry and expiry monitor can arrive
    # together. Expiry must release the ongoing reservation before the next
    # risk gate can consume the freed 30k.
    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"
    assert trade.exit_reason == "EXPIRY_CLOSE"

    assert AlertService().dispatch(
        db_session, _event()
    ) == 0

    ongoing = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "ONGOING",
    ).all()
    assert len(ongoing) == 1
    assert ongoing[0].event_id == "AFTER-EXPIRY-RACE"
    assert ongoing[0].capital_used == 30000

    # The first allocation remains part of today's daily usage, so the second
    # entry is only possible because max_daily_capital was deliberately set to
    # 60k. There is exactly one ongoing 30k reservation and no double reserve.
    assert sum(
        float(row.capital_used)
        for row in db_session.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    ) == 30000


def test_expiry_loss_is_realized_before_next_entry_max_loss_gate(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    svc = LivePaperTradeService()
    svc.mark(db_session, trade, edge=0.0, pnl_override=-100.0)

    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"
    assert trade.realized_pnl == -100.0

    # max_loss=100 is now exactly consumed by the expiry loss. The next entry
    # must be rejected even though the global 30k ongoing reservation was freed.
    assert AlertService().dispatch(
        db_session, _event("AFTER-EXPIRY-LOSS")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "AFTER-EXPIRY-LOSS",
    ).count() == 0


def test_expiry_commit_false_does_not_commit_caller_pending_work(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    setting.paper_amount = 45000

    closed = LivePaperTradeService().close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
        commit=False,
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"

    # The expiry mutation and the caller's unrelated setting mutation are still
    # inside the caller-owned transaction. A rollback must undo both together.
    db_session.rollback()

    verify = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == trade.id
    ).one()
    verify_setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    assert verify.status == "ONGOING"
    assert verify_setting.paper_amount == 30000


def test_expiry_commit_true_persists_all_expiry_transitions_in_one_boundary(db_session):
    _setup(db_session)
    db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).update({GlobalPaperSetting.paper_amount: 60000})
    db_session.commit()
    first = _expired_trade(db_session)
    second, created = LivePaperTradeService().enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="EXPIRING-2",
        event_id="EXPIRING-AT-BOUNDARY-2",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True

    closed = LivePaperTradeService().close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
        commit=True,
    )
    assert {trade.id for trade in closed} == {first.id, second.id}

    db_session.rollback()
    verify = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "COMPLETED",
    ).all()
    assert {trade.id for trade in verify} == {first.id, second.id}


def test_mcx_expiry_uses_2330_boundary_from_paper_metadata(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000, emergency_stop=False,
    ))
    db_session.commit()

    trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="calendar-spread",
        symbol="CRUDEOIL",
        event_id="MCX-2330",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=100,
        lots=1,
        edge=5,
        capital_used=30000,
        legs=[
            {"instrument": "NEAR", "side": "BUY", "price": 100.0},
            {"instrument": "FAR", "side": "SELL", "price": 105.0},
        ],
        metadata={"exchange": "MCX"},
        user_id=1,
    )
    assert created is True

    assert svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    ) == []
    db_session.refresh(trade)
    assert trade.status == "ONGOING"

    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 23, 30),
    )
    assert len(closed) == 1
    db_session.refresh(trade)
    assert trade.status == "COMPLETED"
    assert trade.exit_reason == "EXPIRY_CLOSE"


def test_four_way_mark_manual_close_expiry_and_new_entry_race_converges(tmp_path):
    """All four same-user paths must serialize without duplicate terminal state."""
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'four-way-paper-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    from app.core.database import Base
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    try:
        setup.add(GlobalPaperSetting(
            user_id=1, enabled=True, paper_amount=60000, emergency_stop=False,
        ))
        setup.add(AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=1, max_loss=1000,
        ))
        setup.commit()
        seed, created = LivePaperTradeService().enter_or_mark(
            setup,
            strategy_id="cash-future", symbol="FOUR-WAY",
            event_id="FOUR-WAY-SEED", direction="LONG",
            expiry="2026-10-04", earliest_expiry="2026-10-04",
            lot_size=10, lots=1, edge=10, capital_used=30000, user_id=1,
        )
        assert created is True
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(4)
    errors = []
    results = []

    def mark_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade = db.query(LivePaperTrade).filter(
                LivePaperTrade.event_id == "FOUR-WAY-SEED",
                LivePaperTrade.user_id == 1,
            ).one()
            LivePaperTradeService().mark(
                db, trade, edge=5, pnl_override=-50,
            )
            db.commit()
            results.append("mark")
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def manual_close_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade = db.query(LivePaperTrade).filter(
                LivePaperTrade.event_id == "FOUR-WAY-SEED",
                LivePaperTrade.user_id == 1,
            ).one()
            LivePaperTradeService().close(db, trade, "MANUAL")
            results.append("manual")
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(
                db, now=datetime(2026, 10, 4, 15, 30),
            )
            results.append(("expiry", len(closed)))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            results.append(
                ("entry", AlertService().dispatch(db, _event("FOUR-WAY-ENTRY")))
            )
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [
        threading.Thread(target=mark_worker),
        threading.Thread(target=manual_close_worker),
        threading.Thread(target=expiry_worker),
        threading.Thread(target=entry_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(results) == 4

    verify = Session()
    try:
        seed_rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "FOUR-WAY-SEED",
        ).all()
        assert len(seed_rows) == 1
        seed = seed_rows[0]
        assert seed.status in {"ONGOING", "COMPLETED"}

        if seed.status == "COMPLETED":
            assert seed.exit_reason in {"MANUAL", "EXPIRY_CLOSE"}
            assert seed.realized_pnl == seed.unrealized_pnl
            # The mark must either win before close or lose its race to the
            # terminal conditional UPDATE; it can never resurrect the trade.
            assert seed.closed_at is not None
        else:
            assert seed.exit_reason is None
            assert seed.closed_at is None

        ongoing = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
        assert len(ongoing) <= 1
        if ongoing:
            assert ongoing[0].event_id in {"FOUR-WAY-SEED", "FOUR-WAY-ENTRY"}
            assert ongoing[0].capital_used == 30000

        # At most one position can consume the single 30k free allocation.
        assert verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "FOUR-WAY-ENTRY",
        ).count() <= 1
    finally:
        verify.close()
        engine.dispose()


def test_four_way_terminal_close_risk_and_new_entry_exact_loss_boundary(tmp_path):
    """Mark/manual-close/expiry/new-entry converge without terminal duplication."""
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'four-way-loss-boundary.db'}",
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
                max_loss=100,
            ))
        seed, created = LivePaperTradeService().enter_or_mark(
            setup,
            strategy_id="calendar-spread", symbol="FOUR-WAY-LOSS",
            event_id="FOUR-WAY-LOSS", direction="LONG",
            expiry="2026-10-04", earliest_expiry="2026-10-04",
            lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
        )
        assert created is True
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(4)
    errors = []
    outcomes = []

    def mark_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade = db.query(LivePaperTrade).filter(
                LivePaperTrade.event_id == "FOUR-WAY-LOSS",
                LivePaperTrade.user_id == 1,
            ).one()
            outcomes.append(("mark", LivePaperTradeService().mark(
                db, trade, edge=0.0, pnl_override=-100.0,
            )))
            db.commit()
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def manual_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade = db.query(LivePaperTrade).filter(
                LivePaperTrade.event_id == "FOUR-WAY-LOSS",
                LivePaperTrade.user_id == 1,
            ).one()
            outcomes.append(("manual", LivePaperTradeService().close(
                db, trade, "MANUAL",
            )))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            outcomes.append(("expiry", LivePaperTradeService().close_expired(
                db, now=datetime(2026, 10, 4, 10, 0), commit=True,
            )))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(
                strategy_id="synthetic-future-cash-carry",
                event_id="FOUR-WAY-NEW",
                symbol="FOUR-WAY-NEW",
                timestamp_ns=200,
                message="four-way exact max loss",
                observed_at=datetime(2026, 10, 4, 15, 30),
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
            outcomes.append(("entry", AlertService().dispatch(db, event)))
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [
        threading.Thread(target=mark_worker),
        threading.Thread(target=manual_worker),
        threading.Thread(target=expiry_worker),
        threading.Thread(target=entry_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        seed_rows = [r for r in rows if r.event_id == "FOUR-WAY-LOSS"]
        new_rows = [r for r in rows if r.event_id == "FOUR-WAY-NEW"]
        assert len(seed_rows) == 1
        assert len(new_rows) <= 1

        seed = seed_rows[0]
        if seed.status == "COMPLETED":
            assert seed.exit_reason in {"MANUAL", "EXPIRY_CLOSE"}
            assert seed.realized_pnl == seed.unrealized_pnl
            assert seed.realized_pnl == -100.0
        else:
            assert seed.status == "ONGOING"
            assert seed.realized_pnl == 0.0
            assert seed.current_edge == 0.0

        if new_rows:
            new = new_rows[0]
            assert new.status == "ONGOING"
            assert new.lots == 1
            assert new.capital_used == 30000
    finally:
        verify.close()
        engine.dispose()
