"""Regression coverage for AlertService transaction isolation across users."""

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, *, capital=30000.0):
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
                "lots": 1,
                "edge": 5,
                "capital_used": capital,
            },
        },
    )


def _user(db, user_id, *, amount=30000.0, max_daily=0.0):
    db.add(GlobalPaperSetting(
        user_id=user_id, enabled=True, paper_amount=amount,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=user_id, strategy_id="cash-future",
        min_gross_profit=0.0, mobile_number="", whatsapp_enabled=False,
        enabled=True, max_daily_capital=max_daily,
        max_simultaneous_positions=5, max_loss=0.0,
    ))
    db.commit()


def test_rejected_first_user_does_not_rollback_committed_prior_user_trade(db_session):
    _user(db_session, 1, max_daily=30000)
    _user(db_session, 2, max_daily=30000)

    service = AlertService()
    # User 1 gets a valid reservation first.
    assert service.dispatch(db_session, _event("U1-FIRST")) == 0
    first = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "U1-FIRST",
    ).one()

    # User 2 is evaluated independently; its successful entry must not cause
    # a later user-1 rejection to roll back either user's committed state.
    assert service.dispatch(db_session, _event("U2-FIRST")) == 0
    second = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 2,
        LivePaperTrade.event_id == "U2-FIRST",
    ).one()

    # Both persisted reservations survive the dispatch loop.
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == "ONGOING"
    assert second.status == "ONGOING"
    assert first.capital_used == 30000.0
    assert second.capital_used == 30000.0


def test_invalid_user_rule_is_skipped_without_dirtying_following_user(db_session):
    _user(db_session, 1, amount=30000)
    _user(db_session, 2, amount=30000)

    # Make user 1's persisted rule invalid. It must be filtered before any
    # risk transaction begins; user 2 remains eligible.
    bad = db_session.query(AlertRule).filter(AlertRule.user_id == 1).one()
    bad.max_loss = float("nan")
    db_session.commit()

    assert AlertService().dispatch(db_session, _event("FOLLOWING-USER")) == 0

    # User 2 is still the only valid target and gets its own reservation.
    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 2,
        LivePaperTrade.event_id == "FOLLOWING-USER",
    ).one()
    assert row.capital_used == 30000.0
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
    ).count() == 0


def test_missing_global_setting_does_not_rollback_other_user_state(db_session):
    _user(db_session, 1, amount=30000)
    _user(db_session, 2, amount=30000)

    # Remove user 1's setting, making its risk lock unavailable.
    db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1,
    ).delete(synchronize_session=False)
    db_session.commit()

    assert AlertService().dispatch(db_session, _event("SETTING-MISSING")) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 2,
        LivePaperTrade.event_id == "SETTING-MISSING",
    ).one()
    assert row.capital_used == 30000.0


def test_notification_failure_does_not_undo_paper_trade(db_session):
    _user(db_session, 1, amount=30000)

    class FailingNotifier:
        configured = True

        def send_text(self, mobile, message):
            raise RuntimeError("provider failure")

    service = AlertService(notifier=FailingNotifier())

    # Paper entry is committed by dispatch's normal transaction. Notification
    # happens afterward, so an outbound provider failure must not erase it.
    assert service.dispatch(db_session, _event("NOTIFY-FAIL")) == 0

    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "NOTIFY-FAIL",
    ).one()
    assert row.status == "ONGOING"
    assert row.capital_used == 30000.0


def test_duplicate_mark_commit_survives_later_risk_rejection(db_session):
    _user(db_session, 1, amount=60000, max_daily=60000)

    service = AlertService()
    assert service.dispatch(db_session, _event("MARK-COMMIT")) == 0
    row = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "MARK-COMMIT",
    ).one()
    original = row.current_edge

    duplicate = _event("MARK-COMMIT", capital=60000)
    duplicate.metadata["paper_trade"]["edge"] = 17
    assert service.dispatch(db_session, duplicate) == 0

    db_session.refresh(row)
    assert row.current_edge == 17
    assert row.capital_used == 30000.0
    assert row.status == "ONGOING"
