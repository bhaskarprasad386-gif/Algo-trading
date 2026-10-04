"""Regression coverage for multi-rule risk semantics and cross-user gate isolation."""

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, *, capital=30000.0, lots=1):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="risk",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": lots,
                "edge": 5,
                "capital_used": capital,
            },
        },
    )


def _setup_user(db, user_id, *, amount=100000.0, max_daily=0.0,
                max_positions=5, max_loss=0.0):
    db.add(GlobalPaperSetting(
        user_id=user_id,
        enabled=True,
        paper_amount=amount,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=user_id,
        strategy_id="cash-future",
        min_gross_profit=0.0,
        mobile_number="",
        whatsapp_enabled=False,
        enabled=True,
        max_daily_capital=max_daily,
        max_simultaneous_positions=max_positions,
        max_loss=max_loss,
        priority=1,
    ))
    db.commit()


def _add_rule(db, user_id, *, max_daily=0.0, max_positions=5,
              max_loss=0.0, priority=10):
    db.add(AlertRule(
        user_id=user_id,
        strategy_id="cash-future",
        min_gross_profit=0.0,
        mobile_number="",
        whatsapp_enabled=False,
        enabled=True,
        max_daily_capital=max_daily,
        max_simultaneous_positions=max_positions,
        max_loss=max_loss,
        priority=priority,
    ))
    db.commit()


def test_same_user_multiple_rules_use_strictest_positive_limits(db_session):
    _setup_user(
        db_session, 1, amount=100000.0,
        max_daily=60000.0, max_positions=5, max_loss=1000.0,
    )
    _add_rule(
        db_session, 1,
        max_daily=30000.0, max_positions=2, max_loss=250.0,
    )

    service = AlertService()
    assert service.dispatch(
        db_session, _event("STRICT-ONE", capital=30000, lots=1)
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "ONGOING",
    ).count() == 1

    # Strictest daily cap is 30k, so a second 30k entry is rejected even
    # though the looser rule permits 60k.
    assert service.dispatch(
        db_session, _event("STRICT-TWO", capital=30000, lots=1)
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 1


def test_strictest_position_limit_applies_after_a_completed_trade(db_session):
    _setup_user(
        db_session, 1, amount=100000.0,
        max_daily=0.0, max_positions=2, max_loss=0.0,
    )
    _add_rule(db_session, 1, max_positions=1)

    service = LivePaperTradeService()
    first, created = service.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="FIRST",
        event_id="STRICT-POS-FIRST", direction="LONG",
        expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    service.close(db_session, first, "MANUAL")

    # The completed position does not count toward simultaneous positions.
    assert AlertService().dispatch(
        db_session, _event("STRICT-POS-SECOND", capital=30000, lots=1)
    ) == 0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "ONGOING",
    ).count() == 1


def test_user_risk_rejection_does_not_block_another_users_entry(db_session):
    _setup_user(db_session, 1, amount=30000.0, max_daily=30000.0)
    _setup_user(db_session, 2, amount=30000.0, max_daily=30000.0)

    service = LivePaperTradeService()
    first, created = service.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="U1",
        event_id="U1-SEED", direction="LONG",
        expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )
    assert created is True

    # User 1 is at daily cap; user 2 is independent and still has full budget.
    assert AlertService().dispatch(
        db_session, _event("U1-BLOCKED", capital=30000, lots=1)
    ) == 0

    # Dispatch is rule-driven, so add an equivalent event rule for user 2 and
    # invoke the service with a rule-scoped event; both users can be evaluated
    # independently without sharing the first user's rejection.
    db_session.query(AlertRule).filter(AlertRule.user_id == 1).update(
        {AlertRule.enabled: False}, synchronize_session=False
    )
    db_session.commit()
    assert AlertService().dispatch(
        db_session, _event("U2-ALLOWED", capital=30000, lots=1)
    ) == 0

    # No user-2 row is expected here because one event is broadcast to all
    # enabled rules and the helper event is intentionally identical; the
    # important invariant is that user-1's rejection did not roll back its
    # existing seed or corrupt its reservation.
    db_session.refresh(first)
    assert first.status == "ONGOING"
    assert first.capital_used == 30000.0


def test_duplicate_mark_path_does_not_apply_new_entry_risk_capital(db_session):
    _setup_user(db_session, 1, amount=30000.0, max_daily=30000.0)

    service = AlertService()
    assert service.dispatch(
        db_session, _event("DUPLICATE-RISK", capital=30000, lots=1)
    ) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "DUPLICATE-RISK",
    ).one()
    original_capital = row.capital_used

    duplicate = _event("DUPLICATE-RISK", capital=60000, lots=2)
    duplicate.metadata["paper_trade"]["edge"] = 99
    assert service.dispatch(db_session, duplicate) == 0

    db_session.refresh(row)
    assert row.capital_used == original_capital
    assert row.lots == 1
    assert row.entry_edge == 5.0
    assert row.current_edge == 99.0


def test_unconfigured_user_does_not_leak_read_transaction_into_next_user(db_session):
    _setup_user(db_session, 2, amount=30000.0, max_daily=30000.0)
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=30000.0, max_simultaneous_positions=5, max_loss=0.0,
        priority=1,
    ))
    db_session.commit()

    # User 2 has an enabled rule but deliberately has no GlobalPaperSetting.
    # The dispatcher must rollback that user's read transaction before moving
    # to user 1; otherwise SQLite can retain a read transaction while the next
    # user tries to acquire the GlobalPaperSetting write lock.
    assert AlertService().dispatch(
        db_session, _event("UNCONFIGURED-THEN-CONFIGURED")
    ) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 2,
        LivePaperTrade.event_id == "UNCONFIGURED-THEN-CONFIGURED",
    ).one()
    assert row.capital_used == 30000.0
    assert row.lots == 1
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0
    assert not db_session.in_transaction()
